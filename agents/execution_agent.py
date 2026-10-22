from __future__ import annotations

from agents.base import AgentContext, AgentFailure, BaseAgent
from core.state import BLOCKED
from tools.test_runner import run_cases, sandbox_status


class TestExecutionAgent(BaseAgent):
    __test__ = False
    key, name = "test_execution", "Test Execution"

    def run(self, ctx: AgentContext) -> None:
        s, rec = ctx.state, ctx.rec
        done = {r["case_id"] for r in s.results if r["status"] != "not_run"}
        todo = [c for c in s.cases if c.get("enabled", True) and c["id"] not in done]
        rec.task = "Run enabled test cases in isolation"
        rec.input_summary = f"{len(todo)} case(s) selected of {len(s.cases)}"
        if not todo:
            rec.output_summary = "Nothing new to run"
            rec.reason = "All enabled cases already have results."
            return
        st = ctx.tool("sandbox_status", "Verify isolation", sandbox_status, ctx.settings, describe=lambda r: f"{r['mode']} available={r['available']}")
        s.sandbox, s.isolation = st, st["label"]
        s.results = [r for r in s.results if r["case_id"] not in {c["id"] for c in todo}]
        if not st["available"]:
            s.results += [{"case_id": c["id"], "status": "not_run", "detail": "Not executed: " + st["reason"], "actual": "", "isolation": st["label"]} for c in todo]
            rec.status = BLOCKED
            rec.output_summary = f"0 of {len(todo)} executed - no isolated environment"
            rec.reason = st["reason"]
            ctx.warn("Untrusted code is never run without isolation. No test was marked as passed.")
            return
        out = ctx.tool("test_runner", f"Execute {len(todo)} cases via {st['mode']}", run_cases, s.request.code, todo, ctx.settings, st,
                       describe=lambda r: f"{len(r['results'])} results in {r['duration_ms']}ms" + (f"; {r['error']}" if r["error"] else ""))
        s.results += out["results"]
        if out["error"]:
            ctx.warn(out["error"])
        counts: dict[str, int] = {}
        for r in out["results"]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        rec.output_summary = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no results"
        if not out["ok"] and not out["results"]:
            raise AgentFailure(out["error"] or "The runner produced no output.")
        rec.reason = f"Executed through {st['label']}. Statuses are the real outcomes reported by the harness."
