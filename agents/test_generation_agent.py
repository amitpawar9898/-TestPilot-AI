from __future__ import annotations

from agents.base import AgentContext, AgentFailure, BaseAgent
from tools.test_generator import assign_ids, case_key, generate_cases, llm_cases


class TestGenerationAgent(BaseAgent):
    __test__ = False  # not a pytest class
    key, name = "test_generation", "Test Generation"

    def run(self, ctx: AgentContext) -> None:
        s, req, rec = ctx.state, ctx.state.request, ctx.rec
        extra = s.iteration > 1
        rec.task = "Generate additional test cases" if extra else "Generate normal, boundary, edge, negative and requirement-based cases"
        focus = s.critic.get("requested_focus") if extra else None
        rec.input_summary = f"{len(s.analysis['functions'])} functions; requirements: {len(req.requirements.splitlines())} line(s); iteration {s.iteration}"
        cases, warns, skipped = ctx.tool(
            "deterministic generator", "Type inference + pools + requirement/doctest oracles", generate_cases,
            s.analysis, req.requirements, req.targets, s.plan.get("categories", []), req.max_tests_per_function,
            stress="stress" in s.plan.get("focus", []), extra_for=focus, existing=s.cases, iteration=s.iteration,
            describe=lambda r: f"{len(r[0])} new cases, {len(r[2])} skipped")
        for w in warns:
            ctx.warn(w)
        s.skipped = skipped if not extra else s.skipped
        usable, why = ctx.settings.llm_status()
        if usable and ctx.llm and not extra:
            try:
                lc, lw = ctx.tool(f"llm:{ctx.settings.provider}", "Semantic test proposals", llm_cases, ctx.llm, req.code, s.analysis,
                                  req.requirements, req.objective, req.targets, describe=lambda r: f"{len(r[0])} valid cases")
                seen = {case_key(c) for c in cases} | {case_key(c) for c in s.cases}
                cases += [c for c in lc if case_key(c) not in seen]
                for w in lw:
                    ctx.warn(w)
                s.llm_used = True
            except Exception as e:  # noqa: BLE001 - LLM problems must never block the free engine
                ctx.warn(f"LLM generation failed ({str(e)[:120]}); continued with the deterministic engine.")
        elif ctx.settings.provider != "none" and not extra:
            ctx.warn(f"LLM not used: {why}")
        if not cases:
            if extra:
                rec.output_summary = "No new distinct cases available"
                rec.reason = "Every candidate follow-up case already exists; coverage cannot be widened further."
                return
            skip_txt = "; ".join(f"{k['name']}: {k['reason']}" for k in skipped[:3])
            raise AgentFailure("No test cases could be generated. " + (" ".join(warns[:2]) or skip_txt or "The code has no top-level functions to test.") +
                               " Check the target name. For behavior checks, move script logic into a callable function and add requirement examples. This tool analyzes Python files and cannot guarantee a fix for arbitrary code.")
        assign_ids(cases, s.cases, s.iteration)
        s.cases += cases
        by_cat: dict[str, int] = {}
        for c in cases:
            by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        rec.output_summary = f"{len(cases)} new cases ({', '.join(f'{v} {k}' for k, v in by_cat.items())}); total {len(s.cases)}"
        rec.reason = ("Expected values come only from your requirements/doctests" + (" and the LLM (unverified)" if s.llm_used else "") +
                      "; all other cases check crash-safety, return type, determinism or input rejection.")
        s.gen_warnings += warns
