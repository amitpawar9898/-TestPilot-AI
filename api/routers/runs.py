from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from api.deps import get_current_user, get_db, require_user
from api.runtime import Runtime
from api.schemas import (AgentEvents, AnalysisResponse, BatchAccepted, BatchStatus, DashboardResponse, ExecutionRequest,
                         FilePayload, FindingList, FixResponse, FixRetryRequest, Health, MessageResponse, ReportResponse,
                         BatchOptions, RunAccepted, RunCreateRequest, RunDetail, RunList, RunStatus, RunSummary,
                         AnalysisPreviewRequest, SampleContent, SampleList, TestCase, TestCaseCreateRequest, TestCaseEditsRequest,
                         TestCaseList, TestResult)
from config.settings import DATA_DIR, REPORTS_DIR, Settings
from core.batch import batch_zip, pair_files, summary_rows
from core.state import AGENT_ORDER, RunRequest
from database.db import Database
from tools.code_analyzer import analyze_source
from tools.report_generator import findings_csv, report_html, report_json, tests_csv
from tools.test_generator import export_pytest, format_expect, parse_expect, validate_case
from tools.training_export import to_chat_jsonl, to_prompt_jsonl

router = APIRouter(prefix="/api", tags=["runs", "reports", "tests", "batch", "dashboard"])


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _uid(user: dict | None) -> int | None:
    return int(user["id"]) if user else None


def _owned(db: Database, run_id: str, user: dict | None):
    run = db.get_run(run_id, user_id=_uid(user))
    if not run:
        raise HTTPException(404, "Run not found.")
    return run


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _run_summary(item: dict) -> dict:
    return {"id": item.get("id", ""), "created_at": str(item.get("created_at") or ""),
            "finished_at": item.get("finished_at"), "project": item.get("project") or "Default project",
            "filename": item.get("filename"), "status": item.get("status", "created"), "verdict": item.get("verdict") or "",
            "tests": int(item.get("tests") or 0), "passed": int(item.get("passed") or 0), "failed": int(item.get("failed") or 0),
            "errors": int(item.get("errors") or 0), "findings": int(item.get("findings") or 0), "iterations": int(item.get("iterations") or 1)}



def _progress(state) -> dict:
    agents = list(state.agents.values())
    total = len(AGENT_ORDER)
    done_statuses = {"completed", "blocked", "failed", "needs_review"}
    completed = sum(agent.status in done_statuses for agent in agents)
    active = next((agent for agent in agents if agent.status == "running"), None)
    if active:
        stage, task = active.name, active.task or active.name
    elif state.status in ("completed", "blocked", "failed"):
        completed = total
        stage, task = "Complete", "Analysis finished"
    else:
        stage, task = "Preparing next stage", "Workflow is continuing"
    try:
        created = datetime.fromisoformat(state.created_at.replace("Z", "+00:00"))
        elapsed = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
    except (TypeError, ValueError):
        elapsed = 0
    return {
        "current_stage": stage, "current_task": task,
        "completed_stages": completed, "total_stages": total,
        "progress_percent": 100 if not active and state.status in ("completed", "blocked", "failed") else round(completed * 100 / total),
        "elapsed_seconds": elapsed,
        "tests_processed": len(state.results),
        "tests_total": sum(case.get("enabled", True) for case in state.cases),
    }


def _file(name: str, media_type: str, content: str | bytes) -> dict:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return {"filename": name, "media_type": media_type, "content_base64": base64.b64encode(raw).decode("ascii")}


@router.get("/health", response_model=Health)
def health():
    return {"status": "ok"}


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(request: Request, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    uid = _uid(user)
    runs = db.list_runs(limit=8, user_id=uid)
    recent = db.list_runs(limit=1, user_id=uid)
    timeline = db.timeline(recent.iloc[0]["id"]) if not recent.empty else pd.DataFrame()
    return {"metrics": db.metrics(uid), "trend": _records(db.runs_trend(user_id=uid)),
            "latest_runs": [_run_summary(x) for x in _records(runs)], "latest_events": _records(db.recent_events(8, uid)),
            "latest_findings": _records(db.get_findings(user_id=uid).head(7)), "timeline": _records(timeline)}


@router.post("/analysis/preview", response_model=AnalysisResponse)
def preview(body: AnalysisPreviewRequest, _: dict | None = Depends(get_current_user)):
    return analyze_source(body.code, body.filename)


@router.post("/runs", response_model=RunAccepted, status_code=202)
def create_run(body: RunCreateRequest, request: Request, user: dict | None = Depends(get_current_user)):
    run_id = uuid.uuid4().hex[:10]
    req = RunRequest(code=body.code, filename=Path(body.filename).name, objective=body.objective, requirements=body.requirements,
                     targets=body.targets, project=body.project, categories=body.categories,
                     max_tests_per_function=body.max_tests_per_function, pause_for_review=body.pause_for_review,
                     auto_fix=body.auto_fix, user_id=_uid(user), fix_mode=body.fix_mode, improve_code=body.improve_code)
    _runtime(request).start_run(request.app.state.db, Settings.load(), req, run_id)
    return {"run_id": run_id, "status": "running"}


@router.get("/runs", response_model=RunList)
def list_runs(request: Request, search: str = "", status: str | None = None, verdict: str | None = None,
              since: str | None = None, limit: int = Query(100, ge=1, le=500),
              user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    return {"items": [_run_summary(x) for x in _records(db.list_runs(search, status, verdict, since, limit, user_id=_uid(user)))]}


@router.get("/runs/{run_id}/status", response_model=RunStatus)
def run_status(run_id: str, request: Request, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    rt = _runtime(request)
    run = db.get_run(run_id, user_id=_uid(user))
    if not run:
        job = rt.job_for(run_id)
        if not job or job.get("owner") != _uid(user):
            raise HTTPException(404, "Run not found.")
        return {"run_id": run_id, "status": job["status"], "iteration": 1, "max_iterations": Settings.load().max_iterations,
                "stop_reason": job.get("error", ""), "finished_at": None,
                "current_stage": "Starting analysis", "current_task": "Preparing workflow",
                "completed_stages": 0, "total_stages": len(AGENT_ORDER), "progress_percent": 0,
                "elapsed_seconds": 0, "tests_processed": 0, "tests_total": 0}
    state = rt.state_for(run_id)
    job = rt.job_for(run_id)
    # Keep polling clients in the running state until the worker has committed
    # its final in-memory state. The orchestrator updates SQLite shortly before
    # the worker swaps this state and clears the running job marker.
    status = (job["status"] if job and job.get("status") in ("running", "failed") else
              state.status if state else run["status"])
    progress = _progress(state) if state else {"current_stage": "Complete" if status != "running" else "Starting analysis",
        "current_task": "Analysis finished" if status != "running" else "Preparing workflow",
        "completed_stages": len(AGENT_ORDER) if status != "running" else 0,
        "total_stages": len(AGENT_ORDER), "progress_percent": 100 if status != "running" else 0,
        "elapsed_seconds": 0, "tests_processed": 0, "tests_total": 0}
    return {"run_id": run_id, "status": status, "iteration": state.iteration if state else run["iterations"],
            "max_iterations": state.max_iterations if state else Settings.load().max_iterations,
            "stop_reason": job.get("error") if job and job.get("status") == "failed" else state.stop_reason if state else run["stop_reason"],
            "finished_at": run["finished_at"], "fix": state.fix if state else (db.get_report(run_id) or {}).get("fix"),
            "improvement": state.improvement if state else db.get_improvement(run_id), **progress}


@router.get("/runs/{run_id}/events", response_model=AgentEvents)
def run_events(run_id: str, after_id: int = 0, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    return {"items": [dict(x) for x in db.get_events(run_id) if int(x.get("id") or 0) > after_id]}


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str, request: Request, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    run = _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    cases = db.get_cases(run_id)
    results = db.get_results(run_id)
    result_by_id = {r["case_id"]: r for r in results}
    for case in cases:
        case["result"] = result_by_id.get(case["id"])
    findings = _records(db.get_findings(run_id))
    for finding in findings:
        finding["case_ids"] = [x for x in str(finding.get("case_ids") or "").split(",") if x]
    rep = db.get_report(run_id)
    fx = dict(state.fix) if state else dict((rep or {}).get("fix") or {})
    if fx:
        fx["improvement"] = state.improvement if state else db.get_improvement(run_id)
    data = {"run": run,
            "analysis": db.get_analysis(run_id) or None, "tests": cases, "results": results, "findings": findings,
            "report": rep, "fix": fx or None, "events": db.get_events(run_id), "plan": state.plan if state else (rep or {}).get("plan")}
    return data


@router.get("/runs/{run_id}/analysis", response_model=AnalysisResponse)
def run_analysis(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    a = db.get_analysis(run_id)
    if not a:
        raise HTTPException(404, "Analysis is not available for this run.")
    return a


@router.get("/runs/{run_id}/findings", response_model=FindingList)
def run_findings(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    items = _records(db.get_findings(run_id))
    for finding in items:
        finding["case_ids"] = [x for x in str(finding.get("case_ids") or "").split(",") if x]
    return {"items": items}


@router.get("/runs/{run_id}/tests", response_model=TestCaseList)
def run_tests(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    cases = db.get_cases(run_id)
    results = {r["case_id"]: r for r in db.get_results(run_id)}
    return {"items": [{**c, "result": results.get(c["id"])} for c in cases]}


@router.get("/runs/{run_id}/tests/download", response_model=FilePayload)
def download_tests(run_id: str, request: Request, format: Literal["pytest", "csv"] = "pytest",
                   user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    run = _owned(db, run_id, user)
    if format == "pytest":
        cases = db.get_cases(run_id)
        if not cases:
            raise HTTPException(404, "No generated tests for this run.")
        state = _runtime(request).state_for(run_id) if request else None
        name = state.request.filename if state else run.get("filename") or "submitted.py"
        exceptions = state.analysis.get("module_exceptions") if state else None
        module = Path(name).stem
        if not module.isidentifier():
            module = "submitted"
        return _file(f"test_{module}.py", "text/x-python", export_pytest(cases, module, exceptions))
    rep = db.get_report(run_id)
    if not rep:
        raise HTTPException(404, "Test results report is not available.")
    return _file(f"testpilot_{run_id}_tests.csv", "text/csv", tests_csv(rep))


@router.post("/runs/{run_id}/tests", response_model=TestCase)
def add_test(run_id: str, body: TestCaseCreateRequest, request: Request, user: dict | None = Depends(get_current_user),
             db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    if not state:
        raise HTTPException(409, "Tests can only be edited while this run is available in this server session.")
    case = {"id": f"TC-{max((int(c['id'].split('-')[-1]) for c in state.cases), default=0) + 1:03d}",
            "iteration": state.iteration, "function": body.function, "name": f"{body.function}: {body.name}",
            "category": "edge", "args": body.args, "kwargs": body.kwargs, "ctor": body.ctor or "()", "setup": body.setup or [],
            "expect": parse_expect(body.expected), "rationale": "Added by you.", "source": "manual",
            "oracle": "user_edit" if body.expected.strip().startswith(("==", "raises")) else "heuristic", "enabled": True}
    problems = validate_case(case, {f["qualname"] for f in state.analysis.get("functions", [])})
    if problems:
        raise HTTPException(400, "; ".join(problems))
    state.cases.append(case)
    db.replace_cases(run_id, state.cases)
    case["result"] = None
    return case


@router.patch("/runs/{run_id}/tests", response_model=TestCaseList)
def edit_tests(run_id: str, body: TestCaseEditsRequest, request: Request, user: dict | None = Depends(get_current_user),
               db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    if not state:
        raise HTTPException(409, "Tests can only be edited while this run is available in this server session.")
    by_id = {c["id"]: c for c in state.cases}
    for edit in body.items:
        c = by_id.get(edit.id)
        if not c:
            raise HTTPException(404, f"Test case {edit.id} was not found.")
        c.update(enabled=edit.enabled, name=edit.name, args=edit.args, kwargs=edit.kwargs, ctor=edit.ctor or "()")
        c["expect"] = parse_expect(edit.expected)
        bad = validate_case(c)
        if bad:
            raise HTTPException(400, "; ".join(bad))
        state.results = [r for r in state.results if r["case_id"] != c["id"]]
    db.replace_cases(run_id, state.cases)
    db.replace_results(run_id, state.results)
    return {"items": [{**c, "result": state.result_for(c["id"])} for c in state.cases]}


@router.post("/runs/{run_id}/execution", response_model=RunAccepted, status_code=202)
def execute_tests(run_id: str, body: ExecutionRequest, request: Request, user: dict | None = Depends(get_current_user),
                  db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    ids = None if body.run_all else body.selected_ids
    if not body.run_all and not ids:
        raise HTTPException(400, "Select at least one test to run.")
    try:
        _runtime(request).continue_run(db, Settings.load(), run_id, ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id, "status": "running"}


@router.get("/runs/{run_id}/fix", response_model=FixResponse)
def run_fix(run_id: str, request: Request, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    fx = dict(state.fix) if state else dict((db.get_report(run_id) or {}).get("fix") or {})
    if not fx:
        raise HTTPException(404, "No fix information is available.")
    fx["improvement"] = state.improvement if state else db.get_improvement(run_id)
    return fx


@router.get("/runs/{run_id}/fix/diff", response_model=FilePayload)
def fix_diff(run_id: str, request: Request, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    fx = run_fix(run_id, request, user, db)
    return _file(f"{run_id}.diff", "text/plain", fx["diff"])


@router.get("/runs/{run_id}/fix/download", response_model=FilePayload)
def fix_download(run_id: str, request: Request, format: Literal["fixed", "diff"] = "fixed",
                 user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    fx = state.fix if state else (db.get_report(run_id) or {}).get("fix")
    if not fx:
        raise HTTPException(404, "No fix information is available.")
    if format == "diff":
        return _file(f"{run_id}.diff", "text/plain", fx.get("diff", ""))
    fixed_source = fx.get("patched_source") or ((state.improvement if state else db.get_improvement(run_id)) or {}).get("fixed_source")
    if not fixed_source:
        raise HTTPException(404, "The fixed source is not retained for this run.")
    filename = state.request.filename if state else _owned(db, run_id, user).get("filename") or "submitted.py"
    return _file(f"fixed_{filename}", "text/x-python", fixed_source)


@router.get("/runs/{run_id}/improvement/download", response_model=FilePayload)
def improvement_download(run_id: str, request: Request, format: Literal["improved", "fixed_diff", "improved_diff"],
                         user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    run = _owned(db, run_id, user)
    state = _runtime(request).state_for(run_id)
    item = state.improvement if state else db.get_improvement(run_id)
    if not item:
        raise HTTPException(404, "No improvement information is available.")
    if format == "improved":
        content = item.get("improved_source", "")
        return _file(f"improved_{run.get('filename') or 'submitted.py'}", "text/x-python", content)
    if format == "fixed_diff":
        diff = item.get("original_to_fixed_diff", "")
    else:
        diff = item.get("fixed_to_improved_diff", "")
    suffix = "original-fixed" if format == "fixed_diff" else "fixed-improved"
    return _file(f"{run_id}-{suffix}.diff", "text/plain", diff)


@router.post("/runs/{run_id}/fix/retry", response_model=RunAccepted, status_code=202)
def retry_fix(run_id: str, body: FixRetryRequest, request: Request, user: dict | None = Depends(get_current_user),
              db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    try:
        _runtime(request).retry_fix(db, Settings.load(), run_id, body.hint)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id, "status": "running"}


@router.get("/runs/{run_id}/report", response_model=ReportResponse)
def run_report(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    rep = db.get_report(run_id)
    if not rep:
        raise HTTPException(404, "Report is not available yet.")
    return rep


@router.get("/runs/{run_id}/report/download", response_model=FilePayload)
def report_download(run_id: str, format: Literal["json", "html", "findings_csv", "tests_csv"],
                    user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    rep = db.get_report(run_id)
    if not rep:
        raise HTTPException(404, "Report is not available yet.")
    settings = Settings.load()
    if format == "json":
        return _file(f"testpilot_{run_id}.json", "application/json", report_json(rep))
    if format == "html":
        return _file(f"testpilot_{run_id}.html", "text/html", report_html(rep, settings.report_include_tests, settings.report_include_ai_notes))
    if format == "findings_csv":
        return _file(f"testpilot_{run_id}_findings.csv", "text/csv", findings_csv(rep))
    return _file(f"testpilot_{run_id}_tests.csv", "text/csv", tests_csv(rep))


@router.post("/runs/{run_id}/report/save", response_model=MessageResponse)
def save_report(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    rep = db.get_report(run_id)
    if not rep:
        raise HTTPException(404, "Report is not available yet.")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"testpilot_{run_id}.html"
    path.write_text(report_html(rep, Settings.load().report_include_tests, Settings.load().report_include_ai_notes), encoding="utf-8")
    return {"message": f"Saved report {path.name}."}


@router.delete("/runs/{run_id}", response_model=MessageResponse)
def delete_run(run_id: str, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _owned(db, run_id, user)
    db.delete_run(run_id)
    return {"message": "Run deleted."}


@router.post("/batch", response_model=BatchAccepted, status_code=202)
async def create_batch(request: Request, files: list[UploadFile] = File(...), requirements: str = Form(""), options: str = Form("{}"),
                      user: dict | None = Depends(get_current_user),
                      db: Database = Depends(get_db)):
    uploads = []
    total = 0
    for f in files:
        content = await f.read()
        total += len(content)
        if total > 20_000_000:
            raise HTTPException(413, "Batch upload exceeds 20 MB.")
        uploads.append((f.filename or "upload.py", content))
    paired, problems = pair_files(uploads)
    if not paired:
        raise HTTPException(400, "; ".join(problems) or "Upload at least one valid Python file.")
    try:
        opts = BatchOptions.model_validate_json(options)
    except Exception as exc:
        raise HTTPException(422, "Batch options did not match the API schema.") from exc
    batch_id = uuid.uuid4().hex[:12]
    template = RunRequest(code="", objective=opts.objective, categories=opts.categories, max_tests_per_function=opts.max_tests_per_function,
                          project=opts.project, auto_fix=opts.auto_fix, fix_mode=opts.fix_mode, user_id=_uid(user))
    _runtime(request).start_batch(db, Settings.load(), paired, template, requirements, batch_id)
    return {"batch_id": batch_id, "status": "running", "files": [name for name, _, _ in paired], "warnings": problems}


@router.get("/batch/{batch_id}", response_model=BatchStatus)
def batch_status(batch_id: str, request: Request, user: dict | None = Depends(get_current_user)):
    batch = _runtime(request).batch_for(batch_id)
    if not batch or batch.get("owner") != _uid(user):
        raise HTTPException(404, "Batch not found.")
    return {"batch_id": batch_id, "status": batch["status"], "runs": [_run_summary({"id": s.run_id, "status": s.status,
            "created_at": s.created_at, "project": s.request.project, "filename": s.request.filename, "verdict": s.verdict,
            "tests": len(s.cases), "passed": sum(1 for x in s.results if x["status"] == "passed"),
            "failed": sum(1 for x in s.results if x["status"] == "failed"), "errors": sum(1 for x in s.results if x["status"] in ("error", "timeout")),
            "findings": len(s.findings), "iterations": s.iteration}) for s in batch["states"]],
            "summary": summary_rows(batch["states"])}


@router.get("/batch/{batch_id}/download", response_model=FilePayload)
def batch_download(batch_id: str, request: Request, user: dict | None = Depends(get_current_user)):
    batch = _runtime(request).batch_for(batch_id)
    if not batch or batch.get("owner") != _uid(user):
        raise HTTPException(404, "Batch not found.")
    if batch["status"] != "completed":
        raise HTTPException(409, "Batch is still running.")
    return _file(f"testpilot_{batch_id}.zip", "application/zip", batch_zip(batch["states"]))


@router.get("/samples", response_model=SampleList)
def samples(_: dict | None = Depends(get_current_user)):
    root = Path(__file__).resolve().parents[2] / "sample"
    py_files = sorted(root.glob("sample_*.py"))
    output = []
    for path in py_files:
        stem = path.stem.removeprefix("sample_")
        requirements = (f"sample_{stem}_requirements.txt" if (root / f"sample_{stem}_requirements.txt").exists()
                        else "sample_requirements.txt" if (root / "sample_requirements.txt").exists() else None)
        output.append({"name": path.stem, "code_name": path.name, "requirements_name": requirements})
    return {"items": output}


@router.get("/samples/{name}", response_model=SampleContent)
def sample_content(name: str, _: dict | None = Depends(get_current_user)):
    if not name.replace("_", "").isalnum():
        raise HTTPException(400, "Invalid sample name.")
    root = Path(__file__).resolve().parents[2] / "sample"
    code_path = root / f"{name}.py"
    if not code_path.is_file() or code_path.parent != root:
        raise HTTPException(404, "Sample not found.")
    stem = name.removeprefix("sample_")
    req_path = root / f"sample_{stem}_requirements.txt"
    if not req_path.exists():
        req_path = root / "sample_requirements.txt"
    return {"filename": code_path.name, "code": code_path.read_text(encoding="utf-8"),
            "requirements": req_path.read_text(encoding="utf-8") if req_path.exists() else ""}
