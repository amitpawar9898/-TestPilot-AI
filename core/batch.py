"""Batch mode: analyze, test and fix several independent .py files in one go."""
from __future__ import annotations

import csv
import dataclasses
import io
import zipfile
from pathlib import PurePosixPath

MAX_FILES = 25
MAX_CHARS = 200_000


def pair_files(uploads: list[tuple[str, bytes]]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """uploads: (filename, raw bytes). Pairs `x.py` with an optional `x_requirements.txt` / `x.req.txt` / `x.txt`.
    Returns ([(name, code, requirements)], problems)."""
    problems: list[str] = []
    txt: dict[str, str] = {}
    py: list[tuple[str, str]] = []
    for name, raw in uploads:
        name = PurePosixPath(name.replace("\\", "/")).name
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: not valid UTF-8 text, skipped.")
            continue
        if name.endswith(".py"):
            if len(text) > MAX_CHARS:
                problems.append(f"{name}: {len(text):,} characters exceeds the {MAX_CHARS:,} limit, skipped.")
            elif text.strip():
                py.append((name, text))
            else:
                problems.append(f"{name}: empty file, skipped.")
        elif name.endswith(".txt"):
            txt[name] = text
    if len(py) > MAX_FILES:
        problems.append(f"Only the first {MAX_FILES} files are processed.")
        py = py[:MAX_FILES]
    out = []
    for name, code in py:
        stem = name[:-3]
        req = next((txt[k] for k in (f"{stem}_requirements.txt", f"{stem}.req.txt", f"{stem}.txt") if k in txt), "")
        out.append((name, code, req))
    return out, problems


def run_batch(orchestrator, files: list[tuple[str, str, str]], template, shared_requirements: str = "", on_file=None) -> list:
    states = []
    for i, (name, code, req) in enumerate(files):
        request = dataclasses.replace(template, code=code, filename=name, requirements=req or shared_requirements, pause_for_review=False)
        if on_file:
            on_file(i, name, None)
        st = orchestrator.start(request)
        states.append(st)
        if on_file:
            on_file(i, name, st)
    return states


def summary_rows(states: list) -> list[dict]:
    stems = {s.request.filename[:-3] for s in states}
    rows = []
    for s in states:
        fx = s.fix or {}
        b, a = fx.get("before", {}), fx.get("after", {})
        others = sorted({i["module"].split(".")[0] for i in s.analysis.get("imports", [])} & (stems - {s.request.filename[:-3]}))
        rows.append({"File": s.request.filename, "Status": s.status, "Verdict": s.verdict, "Findings": len(s.findings),
                     "Tests": len(s.cases), "Passed before": b.get("passed", ""), "Passed after": a.get("passed", ""),
                     "Fix": fx.get("status", "-"), "Still failing": len(fx.get("unresolved", [])),
                     "Note": ("Imports another uploaded file (" + ", ".join(others) + "); cross-file imports are not available while testing." if others else "")})
    return rows


def batch_zip(states: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for s in states:
            fx = s.fix or {}
            if fx.get("patched_source"):
                z.writestr(f"fixed/{s.request.filename}", fx["patched_source"])
                z.writestr(f"diffs/{s.request.filename}.diff", fx.get("diff", ""))
        out = io.StringIO()
        rows = summary_rows(states)
        if rows:
            w = csv.DictWriter(out, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        z.writestr("summary.csv", out.getvalue())
        z.writestr("README.txt", "fixed/  patched files (review the diffs first; originals were not modified)\ndiffs/  unified diffs\nsummary.csv  one row per file\n")
    return buf.getvalue()
