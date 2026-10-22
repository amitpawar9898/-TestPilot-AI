"""Workflow definition: stage order and the conditional transitions between agents."""
from __future__ import annotations

from core.state import FAILED, RunState

PIPELINE = ["planner", "code_analysis", "test_generation", "test_execution", "critic", "fix", "improve", "reporting"]
PAUSE = "__pause_for_review__"


def next_stage(state: RunState, finished: str) -> str | None:
    """Decide which agent runs after `finished`. Returns None when the run is over, PAUSE to wait for the user."""
    st = lambda k: state.agents[k].status if k in state.agents else "waiting"  # noqa: E731
    if finished == "planner":
        return "code_analysis" if st("planner") != FAILED else "reporting"
    if finished == "code_analysis":
        if st("code_analysis") == FAILED:  # syntax error: stop testing, still record findings
            state.status, state.stop_reason = "blocked", state.agents["code_analysis"].reason
            return "reporting"
        return "test_generation"
    if finished == "test_generation":
        if state.iteration > 1:
            return "test_execution" if _new_cases(state) else _fix_or_report(state)
        if st("test_generation") == FAILED:
            state.status, state.stop_reason = "blocked", state.agents["test_generation"].reason
            return "reporting"
        if state.request.pause_for_review and not state.review_done:
            return PAUSE
        return "test_execution"
    if finished == "test_execution":
        return "critic"
    if finished == "critic":
        c = state.critic
        if c.get("request_more_tests") and state.iteration < state.max_iterations and st("critic") != FAILED:
            return "planner"  # orchestrator bumps the iteration counter
        return _fix_or_report(state)
    if finished == "fix":
        if state.request.improve_code and st("fix") in ("completed", "needs_review") and not state.fix.get("regressions"):
            return "improve"
        rec = state.agents["improve"]
        reason = ("Code improvement was turned off for this run." if not state.request.improve_code else
                  "Improvement skipped because the fix stage failed or reported regressions.")
        rec.status, rec.reason = "blocked", reason
        state.improvement = {"status": "skipped", "reason": reason}
        return "reporting"
    if finished == "improve":
        return "reporting"
    return None


def _fix_or_report(state: RunState) -> str:
    if state.request.auto_fix:
        return "fix"
    rec = state.agents["fix"]
    rec.status, rec.reason = "blocked", "Auto-fix was turned off for this run."
    return "reporting"


def _new_cases(state: RunState) -> bool:
    done = {r["case_id"] for r in state.results}
    return any(c["id"] not in done for c in state.cases)
