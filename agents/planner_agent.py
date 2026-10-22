from __future__ import annotations

from agents.base import AgentContext, BaseAgent
from tools.code_analyzer import ruff_command
from tools.test_runner import sandbox_status

FOCUS = {
    "boundary": ("boundary", "limit", "edge", "extreme", "empty", "zero", "off-by-one", "min", "max"),
    "negative": ("invalid", "negative", "malformed", "reject", "validation", "robust", "crash", "exception", "error"),
    "stress": ("large", "performance", "big", "scale", "stress", "slow"),
}


class PlannerAgent(BaseAgent):
    key, name = "planner", "Planner"

    def run(self, ctx: AgentContext) -> None:
        s, req, rec = ctx.state, ctx.state.request, ctx.rec
        if s.iteration > 1:
            self._replan(ctx)
            return
        rec.task = "Understand the objective and build an execution plan"
        rec.input_summary = f"Objective: {req.objective[:120]!r}; {len(req.requirements.splitlines())} requirement line(s); targets: {', '.join(req.targets) or 'all functions'}"
        text = f"{req.objective} {req.requirements}".lower()
        focus = [k for k, words in FOCUS.items() if any(w in text for w in words)]
        sb = ctx.tool("sandbox_status", "Check execution environment", sandbox_status, ctx.settings,
                      describe=lambda r: f"{r['mode']}: {'available' if r['available'] else 'unavailable - ' + r['reason'][:70]}")
        ruff = ctx.tool("ruff_check", "Detect Ruff", ruff_command, describe=lambda r: "available" if r else "not installed")
        usable, why = ctx.settings.llm_status()
        gen_tool = f"LLM ({ctx.settings.provider}/{ctx.settings.effective_model()}) + deterministic engine" if usable else "deterministic engine"
        cats = [c for c in req.categories if c in ("normal", "boundary", "edge", "negative")] or ["normal", "boundary", "edge", "negative"]
        steps = [
            {"agent": "Code Analysis", "action": "Parse with AST, inspect functions/params, run static checks", "tools": ["python-ast"] + (["ruff"] if ruff else [])},
            {"agent": "Test Generation", "action": f"Create {', '.join(cats)} cases plus requirement/doctest oracles", "tools": [gen_tool]},
            {"agent": "Test Execution", "action": "Run enabled cases in isolation, capture real outcomes", "tools": [sb["label"]]},
            {"agent": "Critic", "action": "Classify failures vs errors, assess coverage, decide whether another bounded cycle is useful", "tools": ["rule-based critic"] + (["LLM review"] if usable else [])},
            {"agent": "Fix", "action": "Patch known defects (and confirmed failures with an LLM), then re-run the same tests to verify" if req.auto_fix else "Disabled for this run", "tools": ["position-exact patcher"] + (["LLM rewrite"] if usable else [])},
            {"agent": "Reporting", "action": "Merge findings, save report, enable exports", "tools": ["sqlite", "json/csv/html"]},
        ]
        s.plan = {"objective": req.objective, "targets": req.targets or ["all top-level functions"], "focus": focus, "categories": cats,
                  "steps": steps, "max_iterations": s.max_iterations, "iterations": [],
                  "tools": {"static": ["python-ast"] + (["ruff"] if ruff else []), "generator": gen_tool, "execution": sb["label"]}}
        s.sandbox = sb
        rec.output_summary = f"{len(steps)} steps; focus: {', '.join(focus) or 'balanced coverage'}; execution: {sb['mode']}"
        rec.reason = ("Plan built from the objective's keywords and tool availability. " +
                      ("Ruff is not installed, so only built-in AST checks will run. " if not ruff else "") +
                      ("" if sb["available"] else f"Execution unavailable: {sb['reason']}"))
        if not sb["available"]:
            ctx.warn(f"Execution unavailable: {sb['reason']}")
        if not usable and ctx.settings.provider != "none":
            ctx.warn(why)

    def _replan(self, ctx: AgentContext) -> None:
        s, rec = ctx.state, ctx.rec
        crit = s.critic
        rec.task = f"Plan follow-up cycle {s.iteration}"
        rec.input_summary = f"Critic requested more evidence: {crit.get('request_reason', '')[:140]}"
        funcs = crit.get("requested_focus", [])
        s.plan["iterations"].append({"iteration": s.iteration, "functions": funcs, "reason": crit.get("request_reason", "")})
        rec.output_summary = f"Cycle {s.iteration}/{s.max_iterations}: extra tests for {', '.join(funcs)}"
        rec.reason = "The Critic found functions with thin or failing evidence; one more bounded cycle is allowed by the iteration limit."
