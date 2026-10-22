from __future__ import annotations

from agents.base import AgentContext, BaseAgent
from tools.code_improver import improve_source, ollama_improve_source
from tools.fixer import make_diff
from tools.test_runner import run_cases, sandbox_status


class ImproveAgent(BaseAgent):
    key, name = "improve", "Improve"

    def run(self, ctx: AgentContext) -> None:
        state, rec = ctx.state, ctx.rec
        if not state.request.improve_code:
            state.improvement = {"status": "skipped", "reason": "Code improvement was turned off for this run."}
            rec.reason = state.improvement["reason"]
            return
        rec.task = "Apply measurable quality improvements and verify behavior"
        fixed = (state.fix or {}).get("patched_source") or state.request.code
        rec.input_summary = f"Source: {state.request.filename}; improve enabled; {len(state.cases)} generated case(s)"
        result = ctx.tool("ruff_safe_improvements", "Apply safe Ruff edits and calculate before/after metrics",
                          improve_source, fixed, state.request.filename,
                          describe=lambda r: f"{len(r.get('applied', []))} edit(s); {r.get('reason', '')[:120]}")
        ollama = ctx.tool("ollama_local_improvement", "Check for local Ollama and try a bounded behavior-preserving simplification",
                          ollama_improve_source, result.get("source", fixed), state.request.filename,
                          ctx.settings.effective_base_url() if getattr(ctx.settings, "provider", "") == "ollama" else "http://localhost:11434/v1",
                          describe=lambda r: f"{len(r.get('applied', []))} proposal(s); {r.get('reason', '')[:100]}")
        if ollama.get("applied"):
            result = {"source": ollama["source"], "applied": result.get("applied", []) + ollama["applied"],
                      "before_metrics": result.get("before_metrics", ollama["before_metrics"]),
                      "after_metrics": ollama["after_metrics"], "diff": make_diff(fixed, ollama["source"], state.request.filename),
                      "reason": ollama["reason"]}
        if not result.get("applied"):
            state.improvement = {"status": "none", "reason": result.get("reason", "No safe improvements found."),
                                 "original_source": state.request.code, "fixed_source": fixed, "improved_source": fixed,
                                 "original_to_fixed_diff": (state.fix or {}).get("diff", ""), "fixed_to_improved_diff": "",
                                 "before_metrics": result.get("before_metrics", {}), "after_metrics": result.get("after_metrics", {}),
                                 "applied": [], "not_fixed": (state.fix or {}).get("unresolved", [])}
            rec.output_summary = result.get("reason", "No safe improvements found.")
            rec.reason = rec.output_summary
            return

        baseline = state.fix_after_results or state.results
        prior_passed = {r["case_id"] for r in baseline if r.get("status") == "passed"}
        cases = [c for c in state.cases if c["id"] in {r["case_id"] for r in baseline}]
        sb = sandbox_status(ctx.settings)
        rerun = ctx.tool("verify_improvement", "Re-run the same generated test cases on improved code",
                         run_cases, result["source"], cases, ctx.settings, sb,
                         describe=lambda r: f"{sum(x['status'] in ('passed','failed','error','timeout') for x in r['results'])} executed test(s)") if cases else {"results": []}
        rerun_by_id = {r["case_id"]: r for r in rerun["results"]}
        regressions = sorted(case_id for case_id in prior_passed if rerun_by_id.get(case_id, {}).get("status") != "passed")
        if regressions:
            state.improvement = {"status": "rejected", "reason": "Rejected: previously passing test(s) failed after the improvement.",
                                 "fixed_source": fixed, "improved_source": fixed, "fixed_to_improved_diff": "",
                                 "before_metrics": result["before_metrics"], "after_metrics": result["before_metrics"],
                                 "applied": [], "regressions": regressions, "not_fixed": (state.fix or {}).get("unresolved", [])}
            rec.output_summary, rec.reason = state.improvement["reason"], state.improvement["reason"]
            return
        executed = sum(r.get("status") in ("passed", "failed", "error", "timeout") for r in rerun["results"])
        status = "verified" if executed else "unverified"
        reason = result["reason"] if status == "verified" else "Improvement is unverified: tests did not execute (" + (sb.get("reason") or "no test cases available") + ")."
        state.improvement = {"status": status, "reason": reason, "original_source": state.request.code,
                             "original_to_fixed_diff": (state.fix or {}).get("diff", ""),
                             "fixed_source": fixed, "improved_source": result["source"],
                             "fixed_to_improved_diff": result["diff"], "before_metrics": result["before_metrics"],
                             "after_metrics": result["after_metrics"], "applied": result["applied"], "regressions": [],
                             "verification": {"executed": executed, "preserved_passing_cases": len(prior_passed)},
                             "not_fixed": (state.fix or {}).get("unresolved", [])}
        if status == "unverified":
            ctx.warn(reason)
        rec.output_summary = f"{len(result['applied'])} quality edit(s); {status}; Ruff issues {result['before_metrics']['ruff_issues']} → {result['after_metrics']['ruff_issues']}"
        rec.reason = reason
