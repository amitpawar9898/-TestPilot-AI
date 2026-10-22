from __future__ import annotations

from types import SimpleNamespace

from agents.base import AgentRecord
from agents.improve_agent import ImproveAgent
from core.state import RunRequest, RunState
from tools.code_improver import _public_api, improve_source


def test_safe_ruff_edits_are_measurable_and_keep_public_api():
    source = "import math\ndef add(a,b):\n return a+b\n"
    result = improve_source(source, "module.py")
    assert result["applied"]
    assert "import math" not in result["source"]
    assert result["before_metrics"]["ruff_issues"] > result["after_metrics"]["ruff_issues"]
    assert _public_api(source) == _public_api(result["source"])
    assert "F401" in {change["rule"] for change in result["applied"]}


def test_verified_up032_converts_simple_format_calls():
    source = "def greet(name):\n return 'Hello {}'.format(name)\n"
    result = improve_source(source, "module.py")
    assert "f\"Hello {name}\"" in result["source"]
    assert "UP032" in {change["rule"] for change in result["applied"]}


def _context(monkeypatch, source, baseline, fix, availability):
    monkeypatch.setattr("agents.improve_agent.ollama_improve_source", lambda source, filename, base_url: {
        "source": source, "applied": [], "reason": "Local Ollama is not available."})
    request = RunRequest(code=source, filename="module.py", improve_code=True)
    state = RunState(request=request)
    state.cases = [{"id": "case1", "function": "add"}]
    state.results = baseline
    state.fix_after_results = baseline
    state.fix = fix
    state.agents = {"improve": AgentRecord("improve", "Improve")}
    state.improvement = {}
    rec = state.agents["improve"]
    settings = SimpleNamespace()
    def tool(name, summary, fn, *args, **kw):
        kw.pop("describe", None)
        return fn(*args, **kw)
    ctx = SimpleNamespace(state=state, rec=rec, settings=settings, tool=tool,
                          warn=lambda message: None)
    monkeypatch.setattr("agents.improve_agent.sandbox_status", lambda _settings: availability)
    return ctx


def test_behavior_breaking_improvement_is_rejected(monkeypatch):
    original = "def add(a, b):\n    return a + b\n"
    candidate = "def add(a, b):\n    return a - b\n"
    monkeypatch.setattr("agents.improve_agent.improve_source", lambda *_: {
        "source": candidate, "applied": [{"rule": "test", "description": "candidate"}],
        "before_metrics": {"ruff_issues": 1}, "after_metrics": {"ruff_issues": 0}, "diff": "diff", "reason": "candidate"})
    monkeypatch.setattr("agents.improve_agent.run_cases", lambda *_: {"results": [{"case_id": "case1", "status": "failed"}]})
    ctx = _context(monkeypatch, original, [{"case_id": "case1", "status": "passed"}],
                   {"patched_source": original, "unresolved": []}, {"available": True, "reason": ""})
    ImproveAgent().run(ctx)
    assert ctx.state.improvement["status"] == "rejected"
    assert ctx.state.improvement["improved_source"] == original


def test_signature_and_return_annotation_are_part_of_public_api():
    assert _public_api("def f(x: int) -> int:\n return x\n") != _public_api("def f(x: str) -> str:\n return x\n")


def test_unavailable_docker_labels_the_improvement_unverified(monkeypatch):
    original = "import math\ndef add(a,b):\n return a+b\n"
    from tools.code_improver import improve_source
    monkeypatch.setattr("agents.improve_agent.improve_source", improve_source)
    ctx = _context(monkeypatch, original, [{"case_id": "case1", "status": "failed"}],
                   {"patched_source": original, "unresolved": []}, {"available": False, "mode": "docker", "label": "docker unavailable", "reason": "Docker daemon is unavailable"})
    ImproveAgent().run(ctx)
    assert ctx.state.improvement["status"] == "unverified"
    assert "Docker daemon is unavailable" in ctx.state.improvement["reason"]


def test_unfixed_logic_issue_is_carried_forward(monkeypatch):
    source = "import math\ndef add(a,b):\n return a+b\n"
    unresolved = [{"function": "add", "confirmed": 1, "other": 0}]
    monkeypatch.setattr("agents.improve_agent.sandbox_status", lambda _settings: {"available": False, "reason": "offline"})
    ctx = _context(monkeypatch, source, [], {"patched_source": source, "unresolved": unresolved}, {"available": False, "reason": "offline"})
    ImproveAgent().run(ctx)
    assert ctx.state.improvement["not_fixed"] == unresolved
