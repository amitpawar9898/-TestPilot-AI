"""Exercise every API operation on a live server and validate JSON against its OpenAPI schema.

Start FastAPI first, using a disposable TESTPILOT_DATA_DIR. Run this script with the same
TESTPILOT_DATA_DIR so it can seed a learned-fix row needed to exercise the learning routes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from jsonschema import Draft202012Validator, RefResolver

E2E_COVERAGE = {
    ("POST", "/api/auth/register"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"), ("GET", "/api/health"), ("GET", "/api/dashboard"),
    ("GET", "/api/settings"), ("GET", "/api/samples"), ("GET", "/api/samples/{name}"),
    ("POST", "/api/runs"), ("GET", "/api/runs/{run_id}/status"),
    ("GET", "/api/runs/{run_id}/events"), ("GET", "/api/runs/{run_id}/findings"),
    ("GET", "/api/runs/{run_id}/tests"), ("GET", "/api/runs/{run_id}/fix"),
    ("GET", "/api/runs/{run_id}/fix/diff"), ("GET", "/api/runs/{run_id}/fix/download"),
    ("GET", "/api/runs/{run_id}/improvement/download"),
    ("GET", "/api/runs/{run_id}/report"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("TESTPILOT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--output", default="docs/endpoint_verification.json")
    parser.add_argument("--markdown", default="docs/ENDPOINT_VERIFICATION.md")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    client = httpx.Client(base_url=base, timeout=90, follow_redirects=True)
    spec_response = client.get("/openapi.json")
    spec_response.raise_for_status()
    openapi = spec_response.json()
    resolver = RefResolver.from_schema(openapi)
    results: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def hit(method: str, template: str, url: str, *, body: Any = None,
            files: Any = None, data: Any = None, query: Any = None) -> httpx.Response:
        operation = openapi["paths"][template][method.lower()]
        response = client.request(method, url, json=body if files is None else None,
                                  files=files, data=data, params=query)
        if response.status_code // 100 != 2:
            raise AssertionError(f"{method} {template}: {response.status_code} {response.text[:600]}")
        responses = operation.get("responses", {})
        response_doc = responses.get(str(response.status_code)) or responses.get("200")
        if response_doc is None:
            raise AssertionError(f"OpenAPI has no success response schema for {method} {template}")
        content = response_doc.get("content", {})
        json_content = content.get("application/json")
        if json_content:
            instance = response.json()
            schema = json_content["schema"]
            Draft202012Validator(schema, resolver=resolver).validate(instance)
        elif response.content:
            raise AssertionError(f"Unexpected non-JSON response for {method} {template}")
        results[(method.upper(), template)] = {
            "method": method.upper(), "path": template, "status": response.status_code,
            "response_shape": "valid" if json_content else "no-content",
        }
        return response

    def wait_run(run_id: str) -> dict[str, Any]:
        for _ in range(600):
            response = hit("GET", "/api/runs/{run_id}/status", f"/api/runs/{run_id}/status")
            status = response.json()
            if status["status"] not in ("created", "running"):
                return status
            time.sleep(0.1)
        raise AssertionError("Run did not finish within 60 seconds")

    def wait_batch(batch_id: str) -> dict[str, Any]:
        for _ in range(600):
            response = hit("GET", "/api/batch/{batch_id}", f"/api/batch/{batch_id}")
            batch = response.json()
            if batch["status"] not in ("created", "running"):
                return batch
            time.sleep(0.1)
        raise AssertionError("Batch did not finish within 60 seconds")

    suffix = uuid.uuid4().hex[:7]
    username = f"verify_{suffix}"
    password = "VerifyPass123!"
    alt_password = "VerifyPass456!"

    # Public and authentication operations.
    hit("GET", "/api/health", "/api/health")
    hit("POST", "/api/auth/register", "/api/auth/register",
        body={"username": username, "password": password})
    hit("POST", "/api/auth/login", "/api/auth/login", body={"username": username, "password": password})
    hit("GET", "/api/auth/me", "/api/auth/me")
    admin_user = client.get("/api/auth/me").json()
    user_id = admin_user["id"]
    hit("GET", "/api/auth/admin/users", "/api/auth/admin/users")
    created_admin = hit("POST", "/api/auth/admin/users", "/api/auth/admin/users",
                        body={"username": f"admin2_{suffix}", "password": password, "role": "admin"}).json()
    created_regular = hit("POST", "/api/auth/admin/users", "/api/auth/admin/users",
                          body={"username": f"user2_{suffix}", "password": password, "role": "user"}).json()
    hit("PATCH", "/api/auth/admin/users/{user_id}", f"/api/auth/admin/users/{created_regular['id']}",
        body={"role": "admin"})
    hit("PATCH", "/api/auth/admin/users/{user_id}", f"/api/auth/admin/users/{created_regular['id']}",
        body={"role": "user"})
    hit("DELETE", "/api/auth/admin/users/{user_id}", f"/api/auth/admin/users/{created_regular['id']}")
    hit("GET", "/api/auth/admin/settings", "/api/auth/admin/settings")
    hit("PATCH", "/api/auth/admin/settings", "/api/auth/admin/settings",
        body={"require_login": False, "allow_signup": True})
    hit("POST", "/api/auth/logout", "/api/auth/logout")
    hit("POST", "/api/auth/login", "/api/auth/login", body={"username": username, "password": password})
    hit("POST", "/api/auth/password", "/api/auth/password",
        body={"current_password": password, "new_password": alt_password})
    hit("POST", "/api/auth/logout", "/api/auth/logout")
    hit("POST", "/api/auth/login", "/api/auth/login", body={"username": username, "password": alt_password})

    # Settings, sample, preview, and dashboard.
    hit("GET", "/api/dashboard", "/api/dashboard")
    hit("POST", "/api/analysis/preview", "/api/analysis/preview",
        body={"code": "def add(a, b): return a + b", "filename": "preview.py"})
    hit("GET", "/api/samples", "/api/samples")
    sample = hit("GET", "/api/samples/{name}", "/api/samples/sample_buggy").json()
    hit("GET", "/api/settings", "/api/settings")
    hit("PATCH", "/api/settings", "/api/settings", body={"provider": "none", "execution_mode": "docker"})
    hit("GET", "/api/settings/models", "/api/settings/models")
    hit("GET", "/api/settings/availability", "/api/settings/availability")
    hit("POST", "/api/settings/test-connection", "/api/settings/test-connection", body={"provider": "none"})

    # A complete run makes all run-related operations addressable. Default Docker execution
    # remains intact; when Docker is unavailable, result rows are honestly marked not_run.
    run_response = hit("POST", "/api/runs", "/api/runs", body={
        "filename": "verify_sample.py",
        "code": "def add(a, b):\n    return a + b\n\ndef append_item(item, items=[]):\n    items.append(item)\n    return items\n",
        "requirements": "add(2, 3) == 5\nappend_item(3) == [3]", "max_tests_per_function": 3, "auto_fix": True,
    })
    run_id = run_response.json()["run_id"]
    run_status = wait_run(run_id)
    if run_status["status"] not in {"completed", "blocked"}:
        raise AssertionError(f"Run ended unexpectedly: {run_status}")
    hit("GET", "/api/runs", "/api/runs", query={"limit": 20})
    for template, suffix_path in [
        ("/api/runs/{run_id}/events", "events"), ("/api/runs/{run_id}", ""),
        ("/api/runs/{run_id}/analysis", "analysis"), ("/api/runs/{run_id}/findings", "findings"),
        ("/api/runs/{run_id}/tests", "tests"), ("/api/runs/{run_id}/fix", "fix"),
        ("/api/runs/{run_id}/fix/diff", "fix/diff"), ("/api/runs/{run_id}/report", "report"),
    ]:
        hit("GET", template, f"/api/runs/{run_id}/{suffix_path}" if suffix_path else f"/api/runs/{run_id}")
    cases = client.get(f"/api/runs/{run_id}/tests").json()["items"]
    if not cases:
        raise AssertionError("Run did not generate test cases")
    hit("GET", "/api/runs/{run_id}/tests/download", f"/api/runs/{run_id}/tests/download",
        query={"format": "pytest"})
    hit("GET", "/api/runs/{run_id}/tests/download", f"/api/runs/{run_id}/tests/download",
        query={"format": "csv"})
    hit("POST", "/api/runs/{run_id}/tests", f"/api/runs/{run_id}/tests", body={
        "function": "add", "name": "manual add example", "args": "(1, 2)", "kwargs": "{}", "expected": "== 3",
    })
    current_cases = client.get(f"/api/runs/{run_id}/tests").json()["items"]
    first_case = current_cases[0]
    hit("PATCH", "/api/runs/{run_id}/tests", f"/api/runs/{run_id}/tests", body={"items": [{
        "id": first_case["id"], "enabled": first_case["enabled"], "name": first_case["name"],
        "args": first_case["args"], "kwargs": first_case["kwargs"], "ctor": first_case["ctor"], "expected": "no exception",
    }]})
    execution = hit("POST", "/api/runs/{run_id}/execution", f"/api/runs/{run_id}/execution",
                    body={"run_all": True}).json()
    wait_run(run_id)
    hit("GET", "/api/runs/{run_id}/fix/download", f"/api/runs/{run_id}/fix/download",
        query={"format": "fixed"})
    hit("GET", "/api/runs/{run_id}/fix/download", f"/api/runs/{run_id}/fix/download",
        query={"format": "diff"})
    for improvement_format in ("improved", "fixed_diff", "improved_diff"):
        hit("GET", "/api/runs/{run_id}/improvement/download", f"/api/runs/{run_id}/improvement/download",
            query={"format": improvement_format})
    hit("POST", "/api/runs/{run_id}/fix/retry", f"/api/runs/{run_id}/fix/retry",
        body={"hint": "Keep the implementation simple and preserve the behavior."})
    wait_run(run_id)
    for fmt in ("json", "html", "findings_csv", "tests_csv"):
        hit("GET", "/api/runs/{run_id}/report/download", f"/api/runs/{run_id}/report/download",
            query={"format": fmt})
    hit("POST", "/api/runs/{run_id}/report/save", f"/api/runs/{run_id}/report/save")

    # Learning routes need one persisted memory row; seed it using the existing DB access layer.
    from config.settings import DB_PATH
    from database.db import Database

    Database(DB_PATH).add_fix_memory(run_id, "add", "verifier sample", "def add(a,b): return a-b",
                                     "def add(a,b): return a+b", "built-in", "deterministic", user_id=user_id)
    learned = hit("GET", "/api/learning", "/api/learning").json()["items"]
    if not learned:
        raise AssertionError("Learning example seeding failed")
    fix_id = int(learned[0]["id"])
    hit("PATCH", "/api/learning/{fix_id}", f"/api/learning/{fix_id}", body={"rating": 1})
    hit("PATCH", "/api/learning/run/{run_id}", f"/api/learning/run/{run_id}", body={"rating": -1})
    for fmt in ("chat", "prompt"):
        hit("GET", "/api/learning/export", "/api/learning/export", query={"format": fmt, "approved_only": True})
    hit("DELETE", "/api/learning/{fix_id}", f"/api/learning/{fix_id}")
    hit("DELETE", "/api/learning", "/api/learning")

    # Batch upload and retention operations.
    batch_response = hit("POST", "/api/batch", "/api/batch", files=[
        ("files", ("verify_batch.py", b"def add(a,b): return a+b\n", "text/x-python")),
    ], data={"requirements": "add(1,2) == 3", "options": '{"max_tests_per_function":3}'})
    batch_id = batch_response.json()["batch_id"]
    wait_batch(batch_id)
    hit("GET", "/api/batch/{batch_id}/download", f"/api/batch/{batch_id}/download")
    hit("POST", "/api/settings/retention/purge", "/api/settings/retention/purge", body={"days": 3650})
    hit("POST", "/api/settings/retention/scrub-sources", "/api/settings/retention/scrub-sources", body={"days": 3650})

    # Clean up the main run before deleting its account. Keep the second admin as the last admin.
    hit("DELETE", "/api/runs/{run_id}", f"/api/runs/{run_id}")
    hit("DELETE", "/api/auth/account/runs", "/api/auth/account/runs")
    hit("POST", "/api/auth/account/delete", "/api/auth/account/delete", body={"confirm": True})

    expected = {
        (method.upper(), path)
        for path, operations in openapi["paths"].items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    missing = sorted(expected - set(results))
    extra = sorted(set(results) - expected)
    report = {
        "base_url": base, "operation_count": len(expected), "verified_count": len(results),
        "missing": missing, "extra": extra, "results": list(results.values()),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = [
        "# Endpoint verification", "",
        f"Live OpenAPI verification: **{len(results)}/{len(expected)} operations**; missing={len(missing)}, extra={len(extra)}.",
        "Every JSON response body was validated against its OpenAPI response schema. The verifier used a disposable database and synthetic Python code.",
        "The endpoint test server was configured for explicitly trusted local execution of synthetic test fixtures so the fix artifact endpoints could be exercised.",
        "The browser E2E column is filled by `frontend/e2e/workflow.spec.ts`; endpoint rows verified only by HTTP are marked `script`.", "",
        "| Method | Endpoint | Status | Response schema | Verified by |", "|---|---|---:|---|---|",
    ]
    markdown.extend(
        f"| {row['method']} | `{row['path']}` | {row['status']} | {row['response_shape']} | {'script + e2e' if (row['method'], row['path']) in E2E_COVERAGE else 'script'} |"
        for row in report["results"]
    )
    markdown.extend(["", f"Machine-readable results: [`endpoint_verification.json`](endpoint_verification.json)."])
    Path(args.markdown).write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(f"verified={len(results)}/{len(expected)} missing={len(missing)} extra={len(extra)}")
    for row in report["results"]:
        print(f"{row['method']:6} {row['path']:<48} {row['status']} shape={row['response_shape']}")
    print(f"report={args.output}")
    print(f"markdown={args.markdown}")
    client.close()
    return 0 if not missing and not extra else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # Show a short actionable verifier failure, never a successful partial report.
        print(f"verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
