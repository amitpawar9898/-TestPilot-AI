from __future__ import annotations

from agents.base import AgentContext, BaseAgent
from core.state import now
from tools.report_generator import SEV_ORDER, build_report


class ReportingAgent(BaseAgent):
    key, name = "reporting", "Reporting"

    def run(self, ctx: AgentContext) -> None:
        s, rec = ctx.state, ctx.rec
        rec.task = "Merge findings from all agents and save the report"
        static = s.analysis.get("findings", [])
        dynamic = s.critic.get("findings", [])
        rec.input_summary = f"{len(static)} static + {len(dynamic)} test/AI finding(s)"
        merged, seen = [], set()
        for f in static + dynamic:
            k = (f.get("rule"), f.get("line"), f.get("title"), f.get("category"))
            if k in seen:
                continue
            seen.add(k)
            merged.append(dict(f, case_ids=f.get("case_ids", [])))
        merged.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f.get("line") or 0))
        for i, f in enumerate(merged, 1):
            f["finding_id"] = f"F-{s.run_id[:4]}-{i:03d}"
        s.findings = merged
        s.verdict = s.critic.get("verdict") or ("Blocked before testing" if s.status == "blocked" else s.verdict or "Analysis only")
        rep = ctx.tool("build_report", "Assemble structured report", build_report, s, ctx.settings, now(), describe=lambda r: f"{r['summary']['findings']} findings, {r['summary']['cases']} cases")
        s.report = rep
        ctx.tool("sqlite.save_report", "Persist findings and report", self._save, ctx, rep, describe=lambda _: "saved")
        rec.output_summary = f"Report saved: {rep['summary']['findings']} findings, verdict '{s.verdict}'"
        rec.reason = "Report lists exactly what was run, evidence quality per finding and the limitations of this analysis."

    @staticmethod
    def _save(ctx, rep):
        ctx.db.replace_findings(ctx.state.run_id, ctx.state.findings)
        ctx.db.save_report(ctx.state.run_id, rep)
