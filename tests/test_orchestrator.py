from pathlib import Path

from config.settings import Settings
from core.orchestrator import Orchestrator
from core.state import RunRequest
from database.db import Database

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "sample/sample_buggy.py").read_text()
REQ = (ROOT / "sample/sample_requirements.txt").read_text()


def _orch(tmp_path, **kw):
    kw.setdefault("execution_mode", "local_trusted")
    kw.setdefault("per_test_timeout", 2)
    return Orchestrator(Settings(**kw), Database(tmp_path / "t.db")), Database(tmp_path / "t.db")


def test_full_run_finds_confirmed_bugs(tmp_path):
    o, db = _orch(tmp_path)
    st = o.start(RunRequest(code=SRC, requirements=REQ, filename="sample.py"))
    assert st.status == "completed" and st.verdict == "Confirmed defects found"
    assert all(a.status in ("completed", "needs_review") for a in st.agents.values())
    titles = " ".join(f["title"] for f in st.findings)
    assert "apply_discount" in titles and "average" in titles
    assert any(f["category"] == "static" for f in st.findings)
    assert db.get_report(st.run_id)["summary"]["findings"] == len(st.findings)
    assert db.metrics()["runs"] == 1 and db.metrics()["cases"] == len(st.cases)


def test_every_result_comes_from_execution(tmp_path):
    o, _ = _orch(tmp_path)
    st = o.start(RunRequest(code=SRC, requirements=REQ))
    ids = {c["id"] for c in st.cases}
    assert {r["case_id"] for r in st.results} == ids
    assert all(r["status"] in ("passed", "failed", "error", "timeout") for r in st.results)


def test_syntax_error_stops_before_generation_and_execution(tmp_path):
    o, db = _orch(tmp_path)
    st = o.start(RunRequest(code="def f(:\n"))
    assert st.status == "blocked" and not st.cases and not st.results
    assert st.agents["code_analysis"].status == "failed"
    assert st.agents["test_generation"].status == "blocked" and st.agents["test_execution"].status == "blocked"
    assert st.findings[0]["severity"] == "Critical"


def test_disabled_execution_never_reports_passes(tmp_path):
    o, _ = _orch(tmp_path, execution_mode="disabled")
    st = o.start(RunRequest(code=SRC, requirements=REQ))
    assert st.agents["test_execution"].status == "blocked" and st.status == "blocked"
    assert st.results and all(r["status"] == "not_run" for r in st.results)
    assert "Not executed" in st.verdict


def test_unknown_target_fails_generation_with_reason(tmp_path):
    o, _ = _orch(tmp_path)
    st = o.start(RunRequest(code=SRC, targets=["missing_fn"]))
    assert st.agents["test_generation"].status == "failed" and "not found" in st.agents["test_generation"].reason


def test_iterations_are_bounded(tmp_path):
    o, _ = _orch(tmp_path, max_iterations=1)
    st = o.start(RunRequest(code=SRC, requirements=REQ))
    assert st.iteration == 1
    o3, _ = _orch(tmp_path / "x", max_iterations=3)
    assert o3.start(RunRequest(code=SRC, requirements=REQ)).iteration <= 3


def test_pause_for_review_then_continue_with_selection(tmp_path):
    o, db = _orch(tmp_path, max_iterations=1)
    st = o.start(RunRequest(code=SRC, requirements=REQ, pause_for_review=True))
    assert st.status == "awaiting_review" and not st.results
    assert st.agents["test_execution"].status == "waiting"
    pick = [c["id"] for c in st.cases[:3]]
    st = o.continue_run(st, pick)
    assert st.status == "completed" and {r["case_id"] for r in st.results} == set(pick)


def test_agent_crash_is_recorded_not_raised(tmp_path, monkeypatch=None):
    import core.orchestrator as co
    orig = co.AGENTS["critic"].run
    co.AGENTS["critic"].run = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        o, _ = _orch(tmp_path)
        st = o.start(RunRequest(code=SRC, requirements=REQ))
    finally:
        co.AGENTS["critic"].run = orig
    assert st.agents["critic"].status == "failed" and "boom" in st.agents["critic"].reason
    assert st.agents["reporting"].status == "completed"
