from __future__ import annotations

from agents.base import AgentContext, BaseAgent
from tools.code_analyzer import analyze_source
from tools.fixer import (apply_edits, deterministic_edits, function_source, llm_function_fixes, make_diff, parses,
                         replace_function, smart_candidates, validate_function_source, llm_full_source_fix)
from tools.test_generator import CONFIRMED_ORACLES, call_repr, format_expect
from tools.test_runner import run_cases, sandbox_status

EXECUTED = ("passed", "failed", "error", "timeout")


def _counts(results: list[dict]) -> dict:
    c = {k: sum(1 for r in results if r["status"] == k) for k in EXECUTED}
    return {"passed": c["passed"], "failed": c["failed"], "error": c["error"] + c["timeout"], "executed": sum(c.values())}


class FixAgent(BaseAgent):
    key, name = "fix", "Fix"

    def run(self, ctx: AgentContext) -> None:
        s, rec = ctx.state, ctx.rec
        retry = s.fix_retry
        rec.task = "Retry fixing the remaining issues" if retry else "Propose a patch and verify it by re-running the same tests"
        src = s.request.code
        syntax_repair = s.fix.get("syntax_repair")
        cases = {c["id"]: c for c in s.cases}
        executed = [r for r in s.results if r["status"] in EXECUTED and r["case_id"] in cases]
        prior = {r["case_id"]: r["status"] for r in executed}
        zero_div = {cases[r["case_id"]]["function"] for r in executed if r.get("exception_type") == "ZeroDivisionError"}  # methods appear as Class.method
        confirmed_fail: dict[str, list[str]] = {}
        for r in executed:
            c = cases[r["case_id"]]
            if r["status"] in ("failed", "error") and c.get("oracle") in CONFIRMED_ORACLES:
                confirmed_fail.setdefault(c["function"], []).append(
                    f"{call_repr(c)} expected {format_expect(c['expect'])}; outcome: {r.get('detail', '')[:160]}")
        rec.input_summary = (f"{len(s.analysis.get('findings', []))} static finding(s); {len(confirmed_fail)} function(s) with confirmed failures"
                             + (f"; user hint: {s.fix_hint[:80]!r}" if s.fix_hint else ""))
        sb = sandbox_status(ctx.settings)
        can_verify = sb["available"] and bool(prior)
        skipped: list[dict] = []
        if retry and s.fix.get("patched_source"):
            current, accepted, after_results = s.fix["patched_source"], list(s.fix.get("applied", [])), (s.fix_after_results or None)
        else:
            current, accepted, after_results = src, [], None
            if syntax_repair:
                accepted.append(syntax_repair["change"])

        def verify(code: str):
            out = run_cases(code, [cases[i] for i in prior], ctx.settings, sb)
            res = {r["case_id"]: r["status"] for r in out["results"]}
            return out, [i for i, st in prior.items() if st == "passed" and res.get(i) != "passed"]

        # --- 1. deterministic patches (first attempt only) ---------------------------------
        if not retry:
            edits, applied, skipped = ctx.tool("patch_planner", "Plan position-exact edits for known defects", deterministic_edits, src, zero_div,
                                               describe=lambda r: f"{len(r[1])} edit(s), {len(r[2])} skipped")
            if edits:
                patched = apply_edits(src, edits)
                if not parses(patched):
                    ctx.warn("Deterministic patch produced invalid syntax and was discarded.")
                elif can_verify:
                    out, reg = ctx.tool("verify_patch", "Re-run the same tests on the patched code", verify, patched,
                                        describe=lambda r: f"{len(r[0]['results'])} results, {len(r[1])} regression(s)")
                    if reg:
                        skipped.append({"rule": "-", "function": None, "line": None, "reason": f"Rejected: patch broke previously passing tests {', '.join(reg[:5])}."})
                    else:
                        current, accepted, after_results = patched, applied, out["results"]
                else:
                    current, accepted = patched, applied
        # --- 1b. built-in smart rules + memory replay: only kept if the tests prove an improvement -------------
        if can_verify:
            memory = ctx.db.list_fix_memory(include_rejected=False, user_id=s.request.user_id).to_dict("records") if ctx.settings.use_fix_memory else []
            res_now = after_results or executed
            base_bad = sum(1 for r in res_now if r["status"] != "passed")
            for _ in range(6):
                failing = {cases[r["case_id"]]["function"] for r in res_now if r["status"] != "passed" and r["case_id"] in cases}
                took = False
                for desc, trial in smart_candidates(current, failing, memory):
                    if trial == current or not parses(trial):
                        continue
                    out, reg = ctx.tool("verify_patch", f"Try built-in rule {desc['rule']} in {desc['function']}()", verify, trial,
                                        describe=lambda r: f"{len(r[1])} regression(s)")
                    bad_now = sum(1 for r in out["results"] if r["status"] != "passed")
                    if not reg and bad_now < base_bad:
                        current, accepted, after_results, res_now, base_bad, took = trial, accepted + [desc], out["results"], out["results"], bad_now, True
                        break
                if not took:
                    break
        # --- 2. AI rewrites -------------------------------------------------------------------
        usable, why = ctx.settings.llm_status()
        ai_mode = s.request.fix_mode == "ai" or retry
        rounds_done, memory_used, changes = 0, 0, {}
        if ai_mode and not (usable and ctx.llm):
            skipped.append({"rule": "AI", "function": None, "line": None, "reason": f"AI fix mode needs a working provider: {why}"})
        elif usable and ctx.llm and (confirmed_fail or ai_mode):
            max_rounds = min(5, max(1, ctx.settings.max_iterations)) if ai_mode else 1
            cur_results = [dict(r) for r in (after_results or executed)]
            base_bad = sum(1 for r in cur_results if r["status"] != "passed")
            module_functions = analyze_source(current, use_ruff=False)["functions"]
            if ai_mode and not module_functions:
                stdin_unavailable = any(r.get("exception_type") == "EOFError" for r in cur_results)
                evidence = [f"{r['status']}: {r.get('detail', '')[:500]}" for r in cur_results[:12]
                            if not (stdin_unavailable and r.get("exception_type") == "EOFError")]
                if stdin_unavailable:
                    evidence.append("The script smoke runner cannot supply interactive stdin; its EOFError is an execution limitation, not evidence that input() is a bug. Preserve intended input() behavior.")
                evidence.extend(f"{f.get('title')}: {f.get('description')}" for f in s.analysis.get("findings", [])[:20])
                if s.fix_hint:
                    evidence.append(f"User requested: {s.fix_hint[:1000]}")
                try:
                    proposal = ctx.tool(f"llm:{ctx.settings.provider}", "Review and repair the complete Python script",
                                        llm_full_source_fix, ctx.llm, current, s.request.requirements, evidence,
                                        describe=lambda result: "changed module proposed" if result.get("source") != current else "no change proposed")
                    trial = proposal.get("source", current)
                    explanations = proposal.get("explanations") or ["AI reviewed the module and proposed a full-source repair."]
                    if trial != current and parses(trial):
                        if can_verify and not stdin_unavailable:
                            out, reg = ctx.tool("verify_patch", "Re-run tests on the AI-patched script", verify, trial,
                                                describe=lambda result: f"{len(result[0]['results'])} results, {len(result[1])} regression(s)")
                            bad_now = sum(1 for result in out["results"] if result["status"] != "passed")
                            if reg or bad_now > base_bad or (base_bad > 0 and bad_now == base_bad):
                                skipped.append({"rule": "LLM", "function": "__module__", "line": None,
                                                "reason": "AI full-file repair rejected because it regressed tests or did not reduce failures."})
                            else:
                                current, accepted = trial, accepted + [{"rule": "AI-MODULE", "function": "__module__",
                                    "description": "; ".join(explanations)[:1200]}]
                                after_results, cur_results, base_bad = out["results"], out["results"], bad_now
                                rounds_done += 1
                        else:
                            current, accepted = trial, accepted + [{"rule": "AI-MODULE", "function": "__module__",
                                "description": ("; ".join(explanations)[:1200] + " (unverified)")}]
                            rounds_done += 1
                    elif trial != current:
                        skipped.append({"rule": "LLM", "function": "__module__", "line": None,
                                        "reason": "AI full-file repair rejected because it returned invalid Python syntax."})
                except Exception as e:  # noqa: BLE001
                    skipped.append({"rule": "LLM", "function": "__module__", "line": None,
                                    "reason": f"AI full-file repair failed: {str(e)[:160]}"})
            for rd in range(max_rounds if module_functions else 0):
                targets = self._problems(s, cases, cur_results, current, ai_mode)
                if s.fix_hint:
                    names = [t["function"] for t in targets] or [f["qualname"] for f in analyze_source(current, use_ruff=False)["functions"] if not f["is_async"] and not f["name"].startswith("_")]
                    targets = [{"function": n, "evidence": next((t["evidence"] for t in targets if t["function"] == n), "") + f"\n[USER HINT] {s.fix_hint[:600]}"} for n in names]
                if not targets and ai_mode and rd == 0:
                    public_functions = [f["qualname"] for f in analyze_source(current, use_ruff=False)["functions"]
                                        if not f["is_async"] and not f["name"].startswith("_")]
                    targets = [{"function": name, "evidence": (
                        "[AI REVIEW] Review this implementation for clear, reproducible syntax/runtime/logic defects. "
                        "Use stated requirements and executed test evidence when present. Do not guess intended behavior or make style-only edits. "
                        + (f"\n[REQUIREMENTS] {s.request.requirements[:1200]}" if s.request.requirements.strip() else "\nNo behavioral requirements were supplied; only fix defects that are evident from the code.")
                    )} for name in public_functions[:20]]
                if not targets:
                    break
                examples = []
                if ctx.settings.use_fix_memory:
                    for t in targets:
                        for ex in ctx.db.similar_fixes(t["evidence"] + "\n" + function_source(current, t["function"]), 2, user_id=s.request.user_id):
                            if ex["id"] not in {e["id"] for e in examples}:
                                examples.append(ex)
                    memory_used += len(examples)
                    if examples:
                        ctx.tool("fix_memory", "Retrieve similar verified fixes from your history", lambda: examples, describe=lambda r: f"{len(r)} example(s) given to the model")
                try:
                    props = ctx.tool(f"llm:{ctx.settings.provider}", f"AI fix round {rd + 1}/{max_rounds}", llm_function_fixes, ctx.llm, current, targets,
                                     (s.request.requirements if ai_mode else ""), examples, describe=lambda r: f"{len(r)} proposal(s)")
                except Exception as e:  # noqa: BLE001
                    ctx.warn(f"AI fix skipped: {str(e)[:120]}")
                    break
                trial, llm_applied = current, []
                for p in props:
                    err = validate_function_source(p["new_source"], p["function"])
                    new = None if err else replace_function(trial, p["function"], p["new_source"])
                    if not err and (new is None or new == trial):
                        err = "no change or could not be applied"
                    if err or not parses(new):
                        skipped.append({"rule": "LLM", "function": p["function"], "line": None, "reason": f"AI rewrite rejected: {err or 'invalid syntax'}."})
                        continue
                    trial = new
                    node = next((f for f in analyze_source(trial, use_ruff=False)["functions"] if f["qualname"] == p["function"]), None)
                    ev = next((t["evidence"] for t in targets if t["function"] == p["function"]), "")
                    changes.setdefault(p["function"], ev)
                    llm_applied.append({"rule": "LLM", "function": p["function"], "line": node["lineno"] if node else None,
                                        "description": f"AI fix for {p['function']}(): {ev[:240]} (round {rd + 1})" + ("" if can_verify else " - unverified")})
                if not llm_applied:
                    break
                if can_verify:
                    out, reg = ctx.tool("verify_patch", f"Re-run the same tests on the AI-patched code (round {rd + 1})", verify, trial,
                                        describe=lambda r: f"{len(r[1])} regression(s)")
                    bad_now = sum(1 for r in out["results"] if r["status"] != "passed")
                    if reg or bad_now > base_bad or (base_bad > 0 and bad_now == base_bad):
                        why_rej = (f"it broke previously passing tests {', '.join(reg[:5])}." if reg else "it did not reduce the number of failing tests.")
                        skipped.append({"rule": "LLM", "function": None, "line": None, "reason": f"AI rewrite (round {rd + 1}) rejected: {why_rej}"})
                        break
                    current, accepted, after_results, cur_results, base_bad = trial, accepted + llm_applied, out["results"], out["results"], bad_now
                    rounds_done += 1
                else:
                    current, accepted = trial, accepted + llm_applied
                    rounds_done += 1
                    break
        elif confirmed_fail:
            for f in confirmed_fail:
                skipped.append({"rule": "logic", "function": f, "line": None, "reason": "A confirmed requirement failure is a logic error. It needs an AI provider (Settings) or a manual fix; see the finding's evidence."})
        # --- 3. verdict --------------------------------------------------------------------------
        final_results = after_results if after_results is not None else executed
        unresolved = self._unresolved(cases, final_results)
        if not accepted:
            s.fix = {"status": "none", "applied": [], "skipped": skipped, "diff": "", "before": _counts(executed), "after": {}, "regressions": [], "fixed_cases": [],
                     "unresolved": unresolved, "memory_used": memory_used, "hint": s.fix_hint}
            rec.output_summary = "No safe automatic fix" + (f"; {len(unresolved)} function(s) still failing" if unresolved else "")
            rec.reason = "Nothing could be patched without risk. " + (skipped[0]["reason"] if skipped else "No fixable defect was found.") + (" Use 'Fix remaining issues' with a hint." if unresolved else "")
            return
        diff_source = syntax_repair["original"] if syntax_repair else src
        diff = make_diff(diff_source, current, s.request.filename)
        confirmed_ids = {c["id"] for c in cases.values() if c.get("oracle") in CONFIRMED_ORACLES}
        confirmed_results = [r for r in (after_results or []) if r.get("case_id") in confirmed_ids]
        has_verified_expectations = bool(confirmed_results) and all(r["status"] == "passed" for r in confirmed_results)
        status = "verified" if (can_verify and has_verified_expectations and after_results is not None
                                and all(r["status"] == "passed" for r in after_results)) else "unverified"
        after_c = _counts(after_results) if after_results is not None else {}
        fixed = [r["case_id"] for r in (after_results or []) if r["status"] == "passed" and prior.get(r["case_id"]) not in (None, "passed")]
        saved = 0
        if status == "verified" and ctx.settings.learn_from_fixes and changes:
            fixed_fns = {cases[i]["function"] for i in fixed if i in cases}
            for fn, problem in changes.items():
                before, after = function_source(src, fn), function_source(current, fn)
                if fn in fixed_fns and before and after and before != after:
                    ctx.db.add_fix_memory(s.run_id, fn, problem, before, after, ctx.settings.provider, ctx.settings.effective_model(), user_id=s.request.user_id)
                    saved += 1
        s.fix_after_results = after_results or []
        s.fix = {"status": status, "applied": accepted, "skipped": skipped, "diff": diff, "patched_source": current, "before": _counts(executed), "after": after_c,
                 "regressions": [], "fixed_cases": fixed, "ai_rounds": rounds_done, "mode": "ai" if ai_mode else "safe", "unresolved": unresolved,
                 "memory_used": memory_used, "memory_saved": saved, "hint": s.fix_hint,
                 **({"syntax_repair": syntax_repair} if syntax_repair else {}),
                 "static_before": len(analyze_source(src, use_ruff=False)["findings"]), "static_after": len(analyze_source(current, use_ruff=False)["findings"])}
        if status == "unverified":
            ctx.warn("The patch could not be verified by running tests (" + (sb["reason"] or "no executed tests") + "). Review it manually.")
        rec.output_summary = (f"{len(accepted)} fix(es) applied; " + (f"tests: {s.fix['before']['passed']} -> {after_c['passed']} passed, {len(fixed)} newly fixed, 0 regressions"
                                                                       if status == "verified" else "patch is unverified")
                              + (f"; {len(unresolved)} function(s) still failing" if unresolved else "") + (f"; learned {saved} fix(es)" if saved else ""))
        rec.reason = ("The patch was re-tested with the same cases and no previously passing test regressed. " if status == "verified" else
                      "The patch is syntactically valid but was not proven by tests. ") + "Your original file is untouched; review the diff before adopting it."
        rec.status = "needs_review"

    @staticmethod
    def _unresolved(cases, results) -> list[dict]:
        per: dict[str, dict] = {}
        for r in results:
            c = cases.get(r["case_id"])
            if c and r["status"] in ("failed", "error", "timeout"):
                d = per.setdefault(c["function"], {"function": c["function"], "confirmed": 0, "other": 0})
                d["confirmed" if c.get("oracle") in CONFIRMED_ORACLES else "other"] += 1
        return sorted(per.values(), key=lambda d: -d["confirmed"])

    @staticmethod
    def _problems(s, cases, results, current_src, ai_mode) -> list[dict]:
        """Evidence per top-level function. Safe mode: confirmed expectations only. AI mode: every failure, plus serious static findings."""
        funcs = {f["qualname"] for f in analyze_source(current_src, use_ruff=False)["functions"] if not f["is_async"]}
        per: dict[str, list[str]] = {}
        for r in results:
            c = cases.get(r["case_id"])
            if not c or r["status"] not in ("failed", "error", "timeout") or c["function"] not in funcs:
                continue
            confirmed = c.get("oracle") in CONFIRMED_ORACLES
            if not ai_mode and not confirmed:
                continue
            tag = "CONFIRMED" if confirmed else "AUTO-INPUT"
            per.setdefault(c["function"], []).append(f"[{tag}] {call_repr(c)} expected {format_expect(c['expect'])}; "
                                                    f"{r['status']}: {r.get('detail', '')[:160]}")
        if ai_mode:
            for f in s.analysis.get("findings", []):
                fq = next((q for q in funcs if q == f.get("function") or q.endswith("." + str(f.get("function")))), None)
                if fq and f.get("severity") in ("Critical", "High", "Medium") and f.get("category") == "static":
                    per.setdefault(fq, []).append(f"[STATIC {f['severity']}] line {f.get('line')}: {f['title']}")
            if not per and s.request.requirements.strip() and not results:
                for fn in sorted(funcs):
                    per[fn] = ["[REQUIREMENTS] No test evidence; check this function against the stated requirements."]
        return [{"function": fn, "evidence": "\n".join(lines[:6])} for fn, lines in per.items()]
