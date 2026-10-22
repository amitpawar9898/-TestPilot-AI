"""Build and render reports. The stored report JSON is self-contained, so exports work from the database alone."""
from __future__ import annotations

import html
import io
import json

import pandas as pd

from config.settings import APP_NAME, APP_VERSION
from tools.test_generator import format_expect

SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def build_report(state, settings, generated_at: str) -> dict:
    cases = {c["id"]: c for c in state.cases}
    tests = []
    for c in state.cases:
        r = state.result_for(c["id"]) or {"status": "not_run", "detail": "Not executed"}
        tests.append({"id": c["id"], "name": c["name"], "category": c["category"], "function": c["function"], "args": c["args"],
                      "kwargs": c["kwargs"], "expected": format_expect(c["expect"]), "oracle": c.get("oracle"), "source": c.get("source"),
                      "rationale": c.get("rationale"), "status": r["status"], "actual": r.get("actual", ""), "detail": r.get("detail", ""),
                      "duration_ms": r.get("duration_ms", 0), "exception_type": r.get("exception_type", ""), "line": r.get("line")})
    counts = {k: sum(1 for t in tests if t["status"] == k) for k in ("passed", "failed", "error", "timeout", "not_run")}
    executed = counts["passed"] + counts["failed"] + counts["error"] + counts["timeout"]
    sev, cat = {}, {}
    for f in state.findings:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        cat[f["category"]] = cat.get(f["category"], 0) + 1
    cases_by_cat, oracle = {}, {}
    for t in tests:
        cases_by_cat[t["category"]] = cases_by_cat.get(t["category"], 0) + 1
        oracle[t["oracle"] or "unknown"] = oracle.get(t["oracle"] or "unknown", 0) + 1
    funcs_tested = sorted({t["function"] for t in tests if t["status"] != "not_run"})
    limits = [
        "Passing tests show only that the executed inputs behaved as expected or acceptably. They do not prove the code is free of bugs.",
        "Cases with heuristic oracles (no-crash, return type, determinism, input rejection) cannot verify functional correctness; only requirement examples, doctests and user-edited expectations do.",
        "Free-text requirements are interpreted only when an LLM provider is enabled; otherwise write examples such as `f(1, 2) == 3` or `f(-1) raises ValueError`.",
        "Top-level synchronous functions are tested directly. A script with no functions gets one smoke check of top-level execution; that does not verify its output or logic. Class methods and async functions are analyzed statically only.",
        "Results come from an in-process harness; code under test could in principle tamper with it. Treat results for hostile code with caution.",
    ]
    if state.sandbox and not state.sandbox.get("secure", False):
        limits.insert(0, f"Execution environment: {state.isolation or state.sandbox.get('label', 'none')}. This is not a security sandbox." if state.isolation else
                      "No tests were executed because no isolated environment was available.")
    if state.llm_used:
        limits.append("Some test expectations and review notes were produced by an LLM and are unverified.")
    if not state.analysis.get("ruff", {}).get("ran"):
        limits.append("Ruff did not run; only the built-in AST checks were applied.")
    return {
        "meta": {"app": f"{APP_NAME} {APP_VERSION}", "run_id": state.run_id, "project": state.request.project, "generated_at": generated_at,
                 "filename": state.request.filename, "objective": state.request.objective, "isolation": state.isolation or "not executed",
                 "llm": {"provider": settings.provider if state.llm_used else "none", "model": settings.effective_model() if state.llm_used else ""},
                 "iterations": state.iteration, "status": state.status},
        "verdict": state.verdict,
        "summary": {"cases": len(tests), "executed": executed, **counts, "pass_rate": round(100 * counts["passed"] / executed, 1) if executed else None,
                    "findings": len(state.findings), "severity": sev, "categories": cat},
        "scope": {"functions_tested": funcs_tested, "skipped": state.skipped, "cases_by_category": cases_by_cat, "oracle_breakdown": oracle,
                  "stated_requirements": [l.strip() for l in state.request.requirements.splitlines() if l.strip()][:40]},
        "tests": tests, "findings": state.findings,
        "critic": {k: state.critic.get(k) for k in ("verdict", "summary", "recommendations", "weak_tests", "missing", "llm_review")},
        "plan": state.plan, "agents": [{k: a[k] for k in ("name", "status", "iteration", "duration_ms", "output_summary", "reason", "warnings", "errors")}
                                       for a in state.agent_dicts()],
        "fix": {k: v for k, v in (state.fix or {}).items() if k != "patched_source"},
        "improvement": state.improvement or {},
        "limitations": limits, "stop_reason": state.stop_reason,
    }


def tests_csv(report: dict) -> str:
    return pd.DataFrame(report["tests"]).to_csv(index=False)


def findings_csv(report: dict) -> str:
    cols = ["finding_id", "severity", "category", "validation_status", "title", "function", "file", "line", "description", "expected", "actual", "suggested_fix", "evidence"]
    df = pd.DataFrame(report["findings"])
    for c in cols:
        if c not in df:
            df[c] = None
    return df[cols].to_csv(index=False)


def report_json(report: dict) -> str:
    return json.dumps(report, indent=2, default=str)


def report_html(report: dict, include_tests: bool = True, include_ai: bool = True) -> str:
    e = lambda x: html.escape("" if x is None else str(x))  # noqa: E731
    m, s = report["meta"], report["summary"]
    sevc = {"Critical": "#b91c1c", "High": "#dc2626", "Medium": "#d97706", "Low": "#2563eb"}
    rows = "".join(
        f"<tr><td>{e(f['finding_id'])}</td><td><b style='color:{sevc.get(f['severity'], '#444')}'>{e(f['severity'])}</b></td><td>{e(f['category'])}</td>"
        f"<td>{e(f['title'])}<br><small>{e(f['validation_status'])}</small></td><td>{e(f.get('file'))}:{e(f.get('line'))}</td></tr>"
        for f in sorted(report["findings"], key=lambda x: SEV_ORDER.get(x["severity"], 9)) if include_ai or f["category"] != "ai_suggestion")
    details = "".join(
        f"<div class='f'><h4>{e(f['finding_id'])} - {e(f['title'])}</h4><p>{e(f['description'])}</p>"
        f"<p><b>Validation:</b> {e(f['validation_status'])} | <b>Tool:</b> {e(f.get('source_tool'))}</p>"
        + (f"<p><b>Expected:</b> {e(f.get('expected'))}<br><b>Actual:</b> {e(f.get('actual'))}</p>" if f.get("expected") else "")
        + (f"<pre>{e(f.get('evidence'))}</pre>" if f.get("evidence") else "") + f"<p><b>Suggested fix:</b> {e(f.get('suggested_fix'))}</p></div>"
        for f in sorted(report["findings"], key=lambda x: SEV_ORDER.get(x["severity"], 9)) if include_ai or f["category"] != "ai_suggestion")
    tests = ""
    if include_tests:
        tests = "<h2>Test results</h2><table><tr><th>ID</th><th>Name</th><th>Category</th><th>Expected</th><th>Status</th><th>Detail</th></tr>" + "".join(
            f"<tr><td>{e(t['id'])}</td><td>{e(t['name'])}</td><td>{e(t['category'])}</td><td>{e(t['expected'])}</td><td>{e(t['status'])}</td><td>{e(t['detail'])}</td></tr>"
            for t in report["tests"]) + "</table>"
    fx = report.get("fix") or {}
    fixhtml = ""
    if fx.get("status") in ("verified", "unverified", "rejected"):
        b, a = fx.get("before", {}), fx.get("after", {})
        fixhtml = (f"<h2>Proposed fix ({e(fx['status'])})</h2><p>Before: {e(b)}<br>After: {e(a)}<br>Regressions: {e(fx.get('regressions') or 'none')}</p>"
                   + "<ul>" + "".join(f"<li>{e(x['description'])} (line {e(x.get('line'))})</li>" for x in fx.get("applied", [])) + "</ul>"
                   + (f"<pre>{e(fx.get('diff'))}</pre>" if fx.get("diff") else "") + "<p>Review the diff before using it. The original file was not modified.</p>")
    imp = report.get("improvement") or {}
    improvement_html = ""
    if imp:
        improvement_html = (f"<h2>Code quality improvement ({e(imp.get('status'))})</h2><p>{e(imp.get('reason'))}</p>"
                            f"<p>Before metrics: {e(imp.get('before_metrics') or 'not measured')}<br>"
                            f"After metrics: {e(imp.get('after_metrics') or 'not measured')}</p>"
                            + "<ul>" + "".join(f"<li><b>{e(x.get('rule'))}</b>: {e(x.get('description'))}</li>" for x in imp.get("applied", [])) + "</ul>"
                            + (f"<pre>{e(imp.get('fixed_to_improved_diff'))}</pre>" if imp.get("fixed_to_improved_diff") else ""))
    li = lambda xs: "<ul>" + "".join(f"<li>{e(x)}</li>" for x in xs) + "</ul>" if xs else "<p>None.</p>"  # noqa: E731
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{e(m['app'])} report {e(m['run_id'])}</title><style>
body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:980px;margin:32px auto;padding:0 20px;color:#1b1f3b}}
h1{{color:#4338ca}}h2{{border-bottom:2px solid #e0e7ff;padding-bottom:4px;margin-top:32px}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eef2ff}}.k{{display:flex;gap:12px;flex-wrap:wrap}}
.k div{{background:#eef2ff;border-radius:10px;padding:10px 16px}}.k b{{display:block;font-size:22px;color:#4338ca}}pre{{background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;overflow:auto;font-size:12px}}
.f{{border-left:4px solid #6366f1;padding:2px 14px;margin:14px 0;background:#fafaff}}.warn{{background:#fffbeb;border:1px solid #fcd34d;padding:8px 14px;border-radius:8px}}</style></head><body>
<h1>{e(m['app'])} - Test Report</h1><p>Project <b>{e(m['project'])}</b> | file <b>{e(m['filename'])}</b> | run <code>{e(m['run_id'])}</code> | {e(m['generated_at'])}<br>Objective: {e(m['objective'])}<br>Execution: {e(m['isolation'])}</p>
<h2>Verdict</h2><p><b>{e(report['verdict'])}</b></p><div class='k'><div><b>{s['cases']}</b>cases</div><div><b>{s['executed']}</b>executed</div><div><b>{s['passed']}</b>passed</div>
<div><b>{s['failed']}</b>failed</div><div><b>{s['error'] + s['timeout']}</b>errors</div><div><b>{s['findings']}</b>findings</div><div><b>{'n/a' if s['pass_rate'] is None else str(s['pass_rate']) + '%'}</b>pass rate</div></div>
<h2>What was tested</h2><p>Functions: {e(', '.join(report['scope']['functions_tested']) or 'none executed')}<br>Cases by category: {e(report['scope']['cases_by_category'])}<br>Expectation sources: {e(report['scope']['oracle_breakdown'])}</p>
<h2>Findings</h2><table><tr><th>ID</th><th>Severity</th><th>Category</th><th>Title</th><th>Location</th></tr>{rows}</table>{details}
<h2>Critic notes</h2><p>{e(report['critic'].get('summary'))}</p>{li(report['critic'].get('recommendations') or [])}
{('<p><b>AI review (unverified):</b> ' + e(report['critic'].get('llm_review')) + '</p>') if include_ai and report['critic'].get('llm_review') else ''}
{fixhtml}{improvement_html}{tests}<h2>Limitations</h2><div class='warn'>{li(report['limitations'])}</div></body></html>"""
