from __future__ import annotations

from agents.base import AgentContext, BaseAgent
from tools.test_generator import CONFIRMED_ORACLES, call_repr, format_expect

EXECUTED = ("passed", "failed", "error", "timeout")


_call = call_repr


class CriticAgent(BaseAgent):
    key, name = "critic", "Critic & Validation"

    def run(self, ctx: AgentContext) -> None:
        s, rec = ctx.state, ctx.rec
        cases = {c["id"]: c for c in s.cases}
        rows = [(cases[r["case_id"]], r) for r in s.results if r["case_id"] in cases and r["status"] in EXECUTED]
        rec.task = "Review evidence, classify outcomes and judge test quality"
        rec.input_summary = f"{len(rows)} executed result(s), {len(s.analysis.get('findings', []))} static finding(s)"
        if not rows:
            s.critic = {"verdict": "Not executed", "summary": "No test was executed, so there is no dynamic evidence about this code.",
                        "findings": [], "recommendations": ["Enable an isolated execution environment (Docker) and run the tests."],
                        "request_more_tests": False, "requested_focus": [], "request_reason": "", "coverage": {}, "weak_tests": [],
                        "missing": [], "llm_review": None}
            rec.output_summary = "No executed tests to review"
            rec.reason = "Nothing was executed, so the Critic makes no claim about correctness."
            return
        findings = ctx.tool("classify_outcomes", "Separate assertion failures, errors, robustness issues", self._classify, s, rows,
                            describe=lambda r: f"{len(r)} finding(s)")
        cov = self._coverage(s, rows)
        req_more, focus, why = self._more_tests(s, rows, cov)
        confirmed = [f for f in findings if f["validation_status"].startswith("Confirmed")]
        any_bad = any(r["status"] != "passed" for _, r in rows)
        if confirmed and any(f["category"] == "assertion" and f.get("severity") == "High" for f in confirmed):
            verdict = "Confirmed defects found"
        elif any_bad:
            verdict = "Potential issues need review"
        else:
            verdict = "No failures observed (not proof of correctness)"
        recs = list(cov["recommendations"])
        if any_bad:
            recs.insert(0, "Inspect the failing cases first; confirmed oracle failures are the most reliable signals.")
        s.critic = {"verdict": verdict, "findings": findings, "coverage": cov["by_function"], "weak_tests": cov["weak"], "missing": cov["missing"],
                    "recommendations": recs, "request_more_tests": req_more, "requested_focus": focus, "request_reason": why, "llm_review": None,
                    "summary": (f"{sum(1 for _, r in rows if r['status'] == 'passed')} passed, {sum(1 for _, r in rows if r['status'] == 'failed')} failed, "
                                f"{sum(1 for _, r in rows if r['status'] in ('error', 'timeout'))} errored of {len(rows)} executed.")}
        usable, _ = ctx.settings.llm_status()
        if usable and ctx.llm:
            try:
                review = ctx.tool(f"llm:{ctx.settings.provider}", "Review evidence (AI, unverified)", self._llm_review, ctx, s, rows,
                                  describe=lambda r: f"{len(r.get('suggestions', []))} suggestion(s)")
                s.critic["llm_review"] = review.get("summary")
                for sug in review.get("suggestions", [])[:5]:
                    s.critic["findings"].append(self._mk("ai_suggestion", str(sug.get("title", "AI suggestion"))[:120], str(sug.get("description", ""))[:500],
                                                         sug.get("severity") if sug.get("severity") in ("Critical", "High", "Medium", "Low") else "Low",
                                                         sug.get("function"), [], "", "", "", str(sug.get("suggested_fix", ""))[:400],
                                                         "Unverified AI suggestion", s, tool=f"llm:{ctx.settings.provider}"))
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"AI review skipped: {str(e)[:120]}")
        rec.output_summary = f"{verdict}; {s.critic['summary']}" + (" Requests another cycle." if req_more else "")
        rec.reason = ("Failures are split by evidence quality: requirement/doctest mismatches are confirmed; auto-generated checks need review. " +
                      ("More evidence is requested: " + why if req_more else "No further cycle requested."))
        if any_bad:
            rec.status = "needs_review"

    # ------------------------------------------------------------------
    def _mk(self, category, title, desc, sev, fn, ids, evidence, expected, actual, fix, validation, s, line=None, tool="harness"):
        f = next((x for x in s.analysis.get("functions", []) if x["qualname"] == fn or x["name"] == fn), None)
        return {"category": category, "title": title, "description": desc, "severity": sev, "file": s.request.filename,
                "line": line or (f["lineno"] if f else None), "function": fn, "rule": None, "evidence": evidence, "expected": expected,
                "actual": actual, "suggested_fix": fix, "validation_status": validation, "source_tool": tool, "case_ids": ids}

    def _classify(self, s, rows) -> list[dict]:
        out, groups = [], {}
        for c, r in rows:
            if r["status"] == "passed":
                continue
            t, oracle = c["expect"]["type"], c.get("oracle")
            evid = f"Call: {_call(c)}\nExpectation: {format_expect(c['expect'])}\nOutcome: {r['status']} - {r.get('detail', '')}"
            if r.get("traceback"):
                evid += "\n" + r["traceback"][-500:]
            if r["status"] == "timeout":
                out.append(self._mk("execution", f"{c['function']}() timed out", "The call did not finish within the time limit; possible infinite loop or extreme slowness.", "High",
                                    c["function"], [c["id"]], evid, "Completes quickly", r.get("detail", ""), "Check loop termination conditions and algorithmic complexity.",
                                    "Confirmed (reproduced)", s, tool="harness"))
            elif r["status"] == "failed" and t in ("equals", "raises"):
                confirmed = oracle in CONFIRMED_ORACLES
                out.append(self._mk("assertion", f"{c['function']}() violates {'requirement' if oracle == 'requirement' else 'expected behavior'}: {_call(c)[:70]}",
                                    "The function returned a different result than the expectation." if t == "equals" else "The function did not raise the expected exception.",
                                    "High" if confirmed else "Medium", c["function"], [c["id"]], evid, format_expect(c["expect"]), r.get("actual", ""),
                                    "Compare the implementation with the requirement; fix the logic or correct the requirement.",
                                    "Confirmed (user-provided oracle)" if confirmed else "Needs review (AI-derived expectation unverified)", s, line=r.get("line")))
            elif r["status"] == "failed" and t in ("returns_type", "deterministic"):
                out.append(self._mk("assertion", f"{c['function']}() {'violates its return annotation' if t == 'returns_type' else 'is not deterministic'}",
                                    r.get("detail", ""), "Medium", c["function"], [c["id"]], evid, format_expect(c["expect"]), r.get("actual", ""),
                                    "Align the implementation with its annotation / remove hidden state." , "Confirmed (property check)", s))
            else:
                key = (c["function"], r.get("exception_type") or r["status"], "robustness" if t == "robust" else "execution")
                groups.setdefault(key, []).append((c, r, evid))
        for (fn, exc, cat), items in groups.items():
            c0, r0, ev0 = items[0]
            ids = [c["id"] for c, _, _ in items]
            inferred = all(c.get("oracle") == "heuristic" for c, _, _ in items)
            if cat == "robustness":
                out.append(self._mk("robustness", f"{fn}() raises unhandled {exc} on invalid input", f"{len(items)} invalid-input case(s) crashed with {exc} instead of a clear TypeError/ValueError.",
                                    "Low", fn, ids, ev0, "Clear rejection (TypeError/ValueError) or graceful handling", r0.get("actual", ""),
                                    "Validate argument types/values at the top of the function and raise ValueError/TypeError with a helpful message.",
                                    "Needs review (expectation is a robustness convention)", s, line=r0.get("line")))
            else:
                out.append(self._mk("execution", f"{fn}() raised {exc} during execution", f"{len(items)} case(s) raised {exc}. " + r0.get("detail", ""),
                                    "Medium" if inferred else "High", fn, ids, ev0, "Call completes", r0.get("actual") or exc,
                                    "Handle the failing input explicitly (guard clause) or document it as unsupported.",
                                    "Reproduced; inputs were auto-selected, confirm they are valid for this function" if inferred else "Confirmed (reproduced)", s, line=r0.get("line")))
        return out

    def _coverage(self, s, rows) -> dict:
        planned = set(s.plan.get("categories", []))
        by_fn: dict[str, dict] = {}
        for c, r in rows:
            d = by_fn.setdefault(c["function"], {"executed": 0, "passed": 0, "categories": set(), "oracle": 0})
            d["executed"] += 1
            d["passed"] += r["status"] == "passed"
            d["categories"].add(c["category"])
            d["oracle"] += c.get("oracle") in CONFIRMED_ORACLES
        tested = {f["qualname"] for f in s.analysis["functions"] if not f["is_async"] and not f["name"].startswith("_")
                  and (not s.request.targets or f["qualname"] in s.request.targets or f["name"] in s.request.targets or f.get("class_name") in s.request.targets)}
        missing, weak, recs = [], [], []
        for fn in sorted(tested - set(by_fn)):
            missing.append(f"{fn}: no executed tests")
        for fn, d in by_fn.items():
            for cat in sorted(planned - d["categories"]):
                missing.append(f"{fn}: no {cat} case executed")
            if d["oracle"] == 0:
                weak.append(f"{fn}: no expected-value assertions (only crash/type/determinism checks)")
            d["categories"] = sorted(d["categories"])
        if weak:
            recs.append("Add requirement examples like `func(1, 2) == 3` (or doctests) so tests verify results, not just absence of crashes.")
        if missing:
            recs.append("Some planned categories are missing for some functions; widen the plan or raise the per-function limit.")
        recs.append("Passing tests only show the executed inputs behaved acceptably; this report does not claim the code is bug-free.")
        return {"by_function": by_fn, "weak": weak, "missing": missing, "recommendations": recs}

    def _more_tests(self, s, rows, cov):
        if s.iteration >= s.max_iterations:
            return False, [], ""
        cand = []
        for fn, d in cov["by_function"].items():
            # A module smoke test has no callable inputs to expand; asking for another
            # cycle here creates an empty test batch and can bypass the Fix stage.
            if fn == "__module__" or fn in s.extra_done:
                continue
            failing = d["executed"] - d["passed"] > 0
            if failing or d["executed"] < 6:
                cand.append((fn, "failures need neighbouring inputs" if failing else "thin coverage"))
        if not cand:
            return False, [], ""
        s.extra_done += [f for f, _ in cand]
        return True, [f for f, _ in cand], "; ".join(sorted({f"{f}: {w}" for f, w in cand}))[:300]

    def _llm_review(self, ctx, s, rows):
        bad = [{"case": c["id"], "call": _call(c), "expect": format_expect(c["expect"]), "outcome": r["status"], "detail": r.get("detail", "")[:160]}
               for c, r in rows if r["status"] != "passed"][:12]
        system = ('You review automated test evidence. Reply ONLY with JSON: {"summary": str, "suggestions": [{"title": str, "description": str, '
                  '"severity": "Critical|High|Medium|Low", "function": str, "suggested_fix": str}]}. Be concise; do not claim certainty.')
        user = f"Source (truncated):\n{s.request.code[:8000]}\n\nRequirements:\n{s.request.requirements[:1500]}\n\nNon-passing results:\n{bad}\n\nPassed: {sum(1 for _, r in rows if r['status'] == 'passed')} of {len(rows)}."
        data = ctx.llm.extract_json(ctx.llm.complete(system, user, max_tokens=1500))
        return data if isinstance(data, dict) else {"summary": None, "suggestions": []}
