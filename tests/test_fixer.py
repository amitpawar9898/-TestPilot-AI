import json
from pathlib import Path

from config.settings import Settings
from core.orchestrator import Orchestrator
from core.state import RunRequest
from database.db import Database
from tools.fixer import apply_edits, deterministic_edits, validate_function_source
from tools.llm_client import LLMClient

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "sample/sample_buggy.py").read_text()
REQ = (ROOT / "sample/sample_requirements.txt").read_text()


def test_deterministic_patches_are_valid_python_and_exact():
    src = "def f(x, b=[]):\n    '''d'''\n    b.append(x)\n    try:\n        return x == None\n    except:\n        return b\n"
    ed, applied, _ = deterministic_edits(src, set())
    out = apply_edits(src, ed)
    compile(out, "x", "exec")
    assert "b=None" in out and "if b is None:" in out and "x is None" in out and "except Exception:" in out
    assert {a["rule"] for a in applied} == {"TP001", "TP002", "TP005"}


def test_division_guard_only_when_zero_division_was_observed():
    src = "def avg(xs):\n    return sum(xs) / len(xs)\n"
    assert deterministic_edits(src, set())[0] == []
    out = apply_edits(src, deterministic_edits(src, {"avg"})[0])
    assert "raise ValueError" in out


def test_unsafe_llm_rewrites_are_rejected():
    assert validate_function_source("def f(x):\n    import os\n    return os.getcwd()\n", "f")
    assert validate_function_source("def f(x):\n    return eval(x)\n", "f")
    assert validate_function_source("def g(x):\n    return x\n", "f")
    assert validate_function_source("def f(x:\n", "f")
    assert validate_function_source("def f(x):\n    return x * 2\n", "f") is None


def _run(tmp_path, llm=None, **kw):
    db = Database(tmp_path / "t.db")
    o = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1, **kw), db)
    if llm:
        o.llm = llm
    return o.start(RunRequest(code=SRC, requirements=REQ, filename="sample.py", fix_mode="ai" if llm else "safe")), db


def test_fix_is_verified_by_rerunning_tests(tmp_path):
    st, db = _run(tmp_path)
    fx = st.fix
    assert fx["status"] == "verified" and fx["after"]["passed"] > fx["before"]["passed"] and not fx["regressions"]
    compile(fx["patched_source"], "x", "exec")
    assert st.request.code == SRC  # the original input is untouched
    assert any("logic error" in s["reason"] for s in fx["skipped"])  # discount bug needs an LLM / human
    assert "diff" in db.get_report(st.run_id)["fix"] and "patched_source" not in db.get_report(st.run_id)["fix"]


class FakeLLM:
    extract_json = staticmethod(LLMClient.extract_json)

    def __init__(self, good=True):
        self.good = good

    def complete(self, system, user, max_tokens=0):
        if "repair" in system:
            body = "price - price * percent / 100" if self.good else "price - price * percent / 10 + 1"
            return json.dumps([{"function": "apply_discount", "new_source": f"def apply_discount(price: float, percent: float) -> float:\n    return {body}\n"}])
        return "[]" if "QA engineer" in system else "{}"


def test_builtin_rules_fix_the_discount_bug_without_any_ai(tmp_path):
    st, _ = _run(tmp_path)
    assert st.fix["status"] == "verified" and "SMART-PERCENT" in [a["rule"] for a in st.fix["applied"]]
    assert "percent / 100" in st.fix["patched_source"] and not st.fix["regressions"]
    req = [st.result_for(c["id"])["status"] for c in st.cases if c["category"] == "requirement"]
    assert req and all(s == "passed" for s in (st.fix_after_results and [r["status"] for r in st.fix_after_results if r["case_id"] in {c["id"] for c in st.cases if c["category"] == "requirement"}]))


def test_smart_rules_are_rolled_back_when_tests_do_not_improve(tmp_path):
    src = "def share(total, percent):\n    return total * percent / 10\n"
    db = Database(tmp_path / "t.db")
    st = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1), db).start(
        RunRequest(code=src, requirements="share(100, 50) == 5.0"))  # the user's example agrees with /10, so the rule must not be kept
    assert "/ 100" not in (st.fix.get("patched_source") or "")


def test_bad_llm_fix_that_causes_regressions_is_rejected(tmp_path):
    st, _ = _run(tmp_path, llm=FakeLLM(False), provider="ollama")
    assert "percent / 10 + 1" not in st.fix.get("patched_source", "")
    assert any("AI rewrite" in s["reason"] and "rejected" in s["reason"] for s in st.fix["skipped"])
    assert not any(a["rule"] == "LLM" for a in st.fix["applied"])


def test_no_verification_without_isolation_is_labelled_unverified(tmp_path):
    db = Database(tmp_path / "t.db")
    st = Orchestrator(Settings(execution_mode="disabled"), db).start(RunRequest(code="def f(x, b=[]):\n    b.append(x)\n    return b\n"))
    assert st.fix["status"] == "unverified"


def test_auto_fix_can_be_disabled(tmp_path):
    db = Database(tmp_path / "t.db")
    st = Orchestrator(Settings(execution_mode="local_trusted", max_iterations=1), db).start(RunRequest(code=SRC, requirements=REQ, auto_fix=False))
    assert st.agents["fix"].status == "blocked" and not st.fix


# ---------------------------------------------------------------- AI fix mode
LOGIC_SRC = "def total(price, qty):\n    \"\"\"Total cost.\"\"\"\n    return price * qty - price\n"


class RecordingLLM(FakeLLM):
    def __init__(self):
        self.prompts = []

    def complete(self, system, user, max_tokens=0):
        if "repair" in system:
            self.prompts.append(user)
            return json.dumps([{"function": "total", "new_source": "def total(price, qty):\n    return price * qty\n"}])
        return "[]" if "QA engineer" in system else "{}"


def test_ai_mode_fixes_with_natural_language_requirements_and_verifies(tmp_path):
    llm = RecordingLLM()
    db = Database(tmp_path / "t.db")
    o = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1, provider="ollama"), db)
    o.llm = llm
    st = o.start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", filename="shop.py", fix_mode="ai"))
    assert st.fix["status"] == "verified" and st.fix["mode"] == "ai" and st.fix["ai_rounds"] == 1
    assert "price * qty\n" in st.fix["patched_source"] and "- price" not in st.fix["patched_source"]
    assert "total(2, 3) == 6" in llm.prompts[0]  # requirements reach the model


def test_ai_mode_without_provider_explains_instead_of_guessing(tmp_path):
    db = Database(tmp_path / "t.db")
    st = Orchestrator(Settings(execution_mode="local_trusted", max_iterations=1), db).start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", fix_mode="ai"))
    assert st.fix["status"] == "none" and any("needs a working provider" in s["reason"] for s in st.fix["skipped"])


def test_keys_come_only_from_the_environment_and_are_redacted():
    import os
    from config import settings as cs
    os.environ["ANTHROPIC_API_KEY"] = "sk-env-abcdef123456"
    try:
        assert cs.get_secret("ANTHROPIC_API_KEY") == "sk-env-abcdef123456"
        assert "sk-env-abcdef123456" not in cs.redact("error with sk-env-abcdef123456")
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
    assert not hasattr(cs, "session_key_input")  # no in-app key entry exists


def test_ollama_detection_never_raises():
    from unittest import mock
    from tools.llm_client import detect_ollama
    with mock.patch("tools.llm_client.requests.get", side_effect=OSError("down")):
        assert detect_ollama() == (False, [])
    ok = mock.Mock(); ok.raise_for_status = lambda: None; ok.json = lambda: {"models": [{"name": "qwen2.5-coder"}, {}]}
    with mock.patch("tools.llm_client.requests.get", return_value=ok) as g:
        assert detect_ollama("http://localhost:11434/v1") == (True, ["qwen2.5-coder"])
        assert g.call_args[0][0] == "http://localhost:11434/api/tags"



# ------------------------------------------------- learning memory, retry, export
class HintLLM(FakeLLM):
    """First attempt is unhelpful; with a user hint it produces the right fix. Records prompts."""

    def __init__(self):
        self.prompts = []

    def complete(self, system, user, max_tokens=0):
        if "repair" in system:
            self.prompts.append(user)
            body = "price * qty" if "USER HINT" in user else "price * qty - price + 0"
            return json.dumps([{"function": "total", "new_source": f"def total(price, qty):\n    return {body}\n"}])
        return "[]" if "QA engineer" in system else "{}"


def _orch(tmp_path, llm, **kw):
    db = Database(tmp_path / "t.db")
    o = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1, provider="ollama", **kw), db)
    o.llm = llm
    return o, db


def test_retry_with_hint_resolves_what_the_first_attempt_could_not(tmp_path):
    o, db = _orch(tmp_path, HintLLM())
    st = o.start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", fix_mode="ai"))
    assert st.fix["status"] == "none" and st.fix["unresolved"][0]["function"] == "total"
    st = o.retry_fix(st, "total is just price times quantity")
    assert st.fix["status"] == "verified" and "price * qty\n" in st.fix["patched_source"] and not st.fix["unresolved"]
    assert st.fix["hint"] and st.status == "completed"


def test_learning_is_opt_in_and_only_keeps_verified_fixes(tmp_path):
    o, db = _orch(tmp_path / "off", RecordingLLM())
    o.start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", fix_mode="ai"))
    assert db.list_fix_memory().empty  # default: nothing stored
    o, db = _orch(tmp_path / "on", RecordingLLM(), learn_from_fixes=True)
    o.start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", fix_mode="ai"))
    mem = db.list_fix_memory()
    assert len(mem) == 1 and "- price" in mem.iloc[0]["before_code"] and "- price" not in mem.iloc[0]["after_code"]


def test_learned_fix_is_shown_to_the_model_next_time_unless_rejected(tmp_path):
    o, db = _orch(tmp_path, RecordingLLM(), learn_from_fixes=True)
    o.start(RunRequest(code=LOGIC_SRC, requirements="total(2, 3) == 6", fix_mode="ai"))
    second = RecordingLLM()
    o2 = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1, provider="ollama"), db)
    o2.llm = second
    st = o2.start(RunRequest(code=LOGIC_SRC.replace("total", "total_cost"), requirements="total_cost(2, 3) == 6", fix_mode="ai"))
    assert "Verified fixes from earlier work" in second.prompts[0] and st.fix["memory_used"] == 1
    mid = int(db.list_fix_memory().iloc[0]["id"])
    db.rate_fix_memory(mid, -1)
    assert db.similar_fixes("total price qty", 3) == []  # rejected fixes are never reused


def test_export_formats(tmp_path):
    from tools.training_export import to_chat_jsonl, to_prompt_jsonl
    db = Database(tmp_path / "t.db")
    db.add_fix_memory("r", "f", "problem", "def f(x):\n    return x - 1\n", "def f(x):\n    return x + 1\n")
    df = db.list_fix_memory()
    row = json.loads(to_chat_jsonl(df))
    assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"] and "x + 1" in row["messages"][2]["content"]
    assert "completion" in json.loads(to_prompt_jsonl(df))
    assert to_chat_jsonl(df, approved_only=True) == ""  # nothing approved yet
