import json
from pathlib import Path

from config.settings import Settings
from core.orchestrator import Orchestrator
from core.state import RunRequest
from database.db import Database
from tools.code_analyzer import analyze_source
from tools.fixer import replace_function
from tools.llm_client import LLMClient
from tools.test_generator import export_pytest, generate_cases

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "sample/sample_shop.py").read_text()
REQ = (ROOT / "sample/sample_shop_requirements.txt").read_text()


def _run(tmp_path, llm=None, **kw):
    db = Database(tmp_path / "t.db")
    o = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1, **kw), db)
    if llm:
        o.llm = llm
    return o.start(RunRequest(code=SRC, requirements=REQ, filename="shop.py", fix_mode="ai" if llm else "safe")), db


def test_methods_are_generated_and_executed(tmp_path):
    st, db = _run(tmp_path)
    fns = {c["function"] for c in st.cases}
    assert {"Cart.add", "Cart.total", "Cart.average_item", "Cart.discounted"} <= fns
    assert all(r["status"] in ("passed", "failed", "error", "timeout") for r in st.results)
    assert db.get_cases(st.run_id)[0]["ctor"]  # persisted


def test_stateful_requirements_use_setup_steps_and_find_the_real_bugs(tmp_path):
    st, _ = _run(tmp_path)
    req = {c["name"]: st.result_for(c["id"])["status"] for c in st.cases if c["category"] == "requirement"}
    ok = [s for n, s in req.items() if "total" in n]
    assert ok == ["passed", "passed"]  # setup(add) then total() really ran
    assert [s for n, s in req.items() if "discounted" in n] == ["failed"]
    assert [s for n, s in req.items() if "average_item" in n] == ["failed"]


def test_untestable_classes_are_skipped_with_a_reason():
    a = analyze_source("import threading\nclass W(threading.Thread):\n    def go(self):\n        return 1\n", use_ruff=False)
    cases, _, skipped = generate_cases(a, "", [], ["normal"], 5)
    assert not cases and any("external base class" in s["reason"] for s in skipped)


def test_exported_pytest_handles_methods_and_setup():
    a = analyze_source(SRC, use_ruff=False)
    cases, _, _ = generate_cases(a, REQ, [], ["normal"], 4)
    for i, c in enumerate(cases):
        c["id"] = f"TC-{i:03d}"
    code = export_pytest(cases, "shop")
    compile(code, "t.py", "exec")
    assert "obj.add(10.0, 2)" in code and "m.Cart(" in code


def test_method_replacement_keeps_class_structure():
    out = replace_function(SRC, "Cart.discounted", "def discounted(self, percent: float) -> float:\n    return 0.0\n")
    compile(out, "x", "exec")
    assert "    def discounted(self, percent: float) -> float:\n        return 0.0\n" in out and "def average_item" in out


class MethodFixLLM:
    extract_json = staticmethod(LLMClient.extract_json)

    def complete(self, system, user, max_tokens=0):
        if "repair" in system:
            return json.dumps([
                {"function": "Cart.discounted", "new_source": "def discounted(self, percent: float) -> float:\n    return self.total() - self.total() * percent / 100\n"},
                {"function": "Cart.average_item", "new_source": "def average_item(self) -> float:\n    if not self.items:\n        raise ValueError('cart is empty')\n    return sum(self.items) / len(self.items)\n"}])
        return "[]" if "QA engineer" in system else "{}"


def test_ai_fixes_methods_and_the_fix_is_verified_by_tests(tmp_path):
    st, _ = _run(tmp_path, llm=MethodFixLLM(), provider="ollama")
    fx = st.fix
    assert fx["status"] == "verified" and not fx["unresolved"] and fx["after"]["failed"] == 0 and fx["after"]["error"] == 0
    compile(fx["patched_source"], "x", "exec")
    assert "percent / 100" in fx["patched_source"] and "class Cart" in fx["patched_source"]
