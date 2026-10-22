from __future__ import annotations

import base64
import re
import time

import pytest
from fastapi.testclient import TestClient

import config.settings as config_settings
from api.main import create_app


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    from database.db import Database

    monkeypatch.setattr(config_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config_settings, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setenv("TESTPILOT_ALLOW_UNSAFE_LOCAL", "1")
    monkeypatch.delenv("TESTPILOT_REQUIRE_LOGIN", raising=False)
    config_settings.Settings(execution_mode="local_trusted", max_tests_per_function=3,
                            per_test_timeout=2, total_timeout=20).save()
    db = Database(tmp_path / "api.db")
    app = create_app(db)
    # The router imports these paths by value; keep generated artifacts within pytest's temp directory.
    import api.routers.runs as run_routes
    monkeypatch.setattr(run_routes, "REPORTS_DIR", tmp_path / "reports")
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/auth/register", json={"username": "apiadmin", "password": "StrongPass123!"})
        assert created.status_code == 201
        yield client, db, app


def _wait_run(client: TestClient, run_id: str):
    for _ in range(600):
        response = client.get(f"/api/runs/{run_id}/status")
        if response.status_code == 200 and response.json()["status"] not in ("running", "created"):
            return response.json()
        time.sleep(0.05)
    pytest.fail("Background run did not finish within 30 seconds")


def _make_run(client: TestClient) -> str:
    source = "def append_item(item, items=[]):\n    items.append(item)\n    return items\n"
    response = client.post("/api/runs", json={"code": source, "filename": "append_item.py",
                                              "requirements": "append_item(3) == [3]", "max_tests_per_function": 3})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert _wait_run(client, run_id)["status"] == "completed"
    return run_id


def test_api_auth_settings_and_openapi(api_env):
    client, db, _ = api_env
    from database.db import Database
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/me").json()["username"] == "apiadmin"
    assert client.get("/api/auth/admin/users").status_code == 200
    assert client.get("/api/auth/admin/settings").status_code == 200
    assert client.patch("/api/auth/admin/settings", json={"require_login": False, "allow_signup": True}).status_code == 200
    admin_user = client.post("/api/auth/admin/users", json={"username": "rolechange", "password": "StrongPass789!", "role": "user"})
    assert admin_user.status_code == 201
    target_id = admin_user.json()["id"]
    assert client.patch(f"/api/auth/admin/users/{target_id}", json={"role": "admin"}).status_code == 200
    assert client.patch(f"/api/auth/admin/users/{target_id}", json={"role": "user"}).status_code == 200
    assert client.delete(f"/api/auth/admin/users/{target_id}").status_code == 200
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/settings/models").status_code == 200
    assert client.get("/api/settings/availability").status_code == 200
    assert client.post("/api/settings/test-connection", json={}).status_code == 200
    assert client.patch("/api/settings", json={"max_iterations": 2}).status_code == 200
    assert client.post("/api/settings/retention/purge", json={"days": 3650}).status_code == 200
    assert client.post("/api/settings/retention/scrub-sources").status_code == 200
    assert client.get("/api/samples").status_code == 200
    assert client.get("/api/samples/sample_inventory").status_code == 200
    assert client.post("/api/analysis/preview", json={"code": "def f(): return 1", "filename": "f.py"}).status_code == 200

    # Verify the checked-in contract's method/path inventory against the generated OpenAPI routes.
    spec = client.get("/openapi.json").json()["paths"]
    actual = {(method.upper(), path.removeprefix("/api")) for path, methods in spec.items()
              for method in methods if method.lower() in {"get", "post", "patch", "delete"}}
    contract = (config_settings.BASE_DIR / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")
    expected = set()
    for line in contract.splitlines():
        match = re.match(r"\| (GET|POST|PATCH|DELETE) \| `(/[^`]+)` \|", line)
        if match:
            expected.add((match.group(1), match.group(2)))
    assert actual == expected, {"missing": sorted(expected - actual), "extra": sorted(actual - expected)}

    # Invalid authentication and request validation fail with the stable envelope.
    anon = TestClient(create_app(Database(db.path.parent / "anonymous.db")), raise_server_exceptions=False)
    assert client.post("/api/auth/login", json={"username": "apiadmin", "password": "wrong-password-1"}).status_code == 401
    assert client.post("/api/auth/register", json={"username": "apiadmin", "password": "StrongPass123!"}).status_code == 409
    assert client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "EvenStronger456!"}).status_code == 400
    assert client.post("/api/auth/admin/users", json={"username": "apiadmin", "password": "StrongPass123!", "role": "user"}).status_code == 409
    assert client.patch("/api/auth/admin/users/1", json={"role": "root"}).status_code == 422
    assert client.patch("/api/auth/admin/settings", json={"require_login": True}).status_code == 422
    assert client.post("/api/auth/account/delete", json={"confirm": False}).status_code == 400
    assert client.post("/api/runs", json={"code": ""}).status_code == 422
    assert client.get("/api/runs/missing-id").status_code == 404
    for path in ("/status", "/events", "/analysis", "/findings", "/tests", "/fix", "/fix/diff", "/report"):
        assert client.get(f"/api/runs/missing-id{path}").status_code == 404
    assert client.get("/api/runs/missing-id/tests/download").status_code == 404
    assert client.get("/api/runs/missing-id/fix/download").status_code == 404
    assert client.get("/api/runs/missing-id/report/download?format=json").status_code == 404
    assert client.post("/api/runs/missing-id/tests", json={"function": "f", "name": "case"}).status_code == 404
    assert client.patch("/api/runs/missing-id/tests", json={"items": []}).status_code == 404
    assert client.post("/api/runs/missing-id/execution", json={"run_all": True}).status_code == 404
    assert client.post("/api/runs/missing-id/fix/retry", json={"hint": ""}).status_code == 404
    assert client.post("/api/runs/missing-id/report/save").status_code == 404
    assert client.delete("/api/auth/admin/users/999999").status_code == 404
    assert client.post("/api/health").status_code == 405
    assert client.get("/api/auth/logout").status_code == 405
    assert client.get("/api/analysis/preview").status_code == 405
    assert client.post("/api/analysis/preview", json={"code": ""}).status_code == 422
    delete_client = TestClient(create_app(Database(db.path.parent / "delete-account.db")), raise_server_exceptions=False)
    assert delete_client.post("/api/auth/register", json={"username": "deleteme", "password": "StrongPass987!"}).status_code == 201
    assert delete_client.post("/api/auth/account/delete", json={"confirm": True}).status_code == 200
    assert delete_client.get("/api/auth/me").status_code == 200 and delete_client.get("/api/auth/me").json() is None
    assert client.post("/api/settings/test-connection", json={"provider": "unknown"}).status_code == 400
    assert client.get("/api/samples/not_a_sample").status_code == 404
    assert client.get("/api/batch/missing").status_code == 404
    assert client.get("/api/batch/missing/download").status_code == 404
    assert client.patch("/api/learning/999999", json={"rating": 1}).status_code == 404
    assert client.patch("/api/learning/run/missing", json={"rating": 0}).status_code == 404
    assert client.patch("/api/learning/1", json={"rating": 99}).status_code == 422
    assert client.delete("/api/learning/999999").status_code == 404
    assert client.get("/api/learning/export?format=bad").status_code == 400
    assert client.patch("/api/settings", json={"provider": "mystery"}).status_code == 400
    assert client.post("/api/settings/retention/purge", json={"days": 0}).status_code == 422
    assert db.count_users() == 1


def test_api_run_test_fix_report_and_ownership(api_env):
    client, db, app = api_env
    run_id = _make_run(client)
    assert client.get(f"/api/runs/{run_id}/status").status_code == 200
    assert client.get(f"/api/runs/{run_id}/events").status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert client.get(f"/api/runs/{run_id}/analysis").status_code == 200
    assert client.get(f"/api/runs/{run_id}/findings").status_code == 200
    cases = client.get(f"/api/runs/{run_id}/tests").json()["items"]
    assert cases and client.get(f"/api/runs/{run_id}/tests/download?format=pytest").status_code == 200
    assert client.get(f"/api/runs/{run_id}/tests/download?format=csv").status_code == 200
    assert client.get(f"/api/runs/{run_id}/tests/download?format=unknown").status_code == 422
    assert client.post(f"/api/runs/{run_id}/tests", json={"function": "append_item", "name": "manual", "args": "(4,)",
                                                            "kwargs": "{}", "expected": "== [4]"}).status_code == 200
    current_cases = client.get(f"/api/runs/{run_id}/tests").json()["items"]
    case = current_cases[0]
    edit = {"id": case["id"], "enabled": case["enabled"], "name": case["name"], "args": case["args"],
            "kwargs": case["kwargs"], "ctor": case["ctor"], "expected": "== [3]"}
    assert client.patch(f"/api/runs/{run_id}/tests", json={"items": [edit]}).status_code == 200
    assert client.patch(f"/api/runs/{run_id}/tests", json={"items": [{**edit, "id": "TC-999"}]}).status_code == 404
    fixed = client.get(f"/api/runs/{run_id}/fix/download?format=fixed")
    assert fixed.status_code == 200
    assert "append_item" in base64.b64decode(fixed.json()["content_base64"]).decode()
    assert client.get(f"/api/runs/{run_id}/fix").status_code == 200
    assert client.get(f"/api/runs/{run_id}/fix/diff").status_code == 200
    assert client.get(f"/api/runs/{run_id}/fix/download?format=diff").status_code == 200
    assert client.get(f"/api/runs/{run_id}/fix/download?format=unknown").status_code == 422
    for fmt in ("json", "html", "findings_csv", "tests_csv"):
        assert client.get(f"/api/runs/{run_id}/report/download?format={fmt}").status_code == 200
    assert client.get(f"/api/runs/{run_id}/report").status_code == 200
    assert client.post(f"/api/runs/{run_id}/report/save").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/runs").status_code == 200

    # Per-user run isolation is enforced even for a valid run ID.
    created = client.post("/api/auth/admin/users", json={"username": "seconduser", "password": "StrongPass456!", "role": "user"})
    assert created.status_code == 201
    other = TestClient(app, raise_server_exceptions=False)
    assert other.post("/api/auth/login", json={"username": "seconduser", "password": "StrongPass456!"}).status_code == 200
    assert other.get(f"/api/runs/{run_id}").status_code == 404
    assert other.get(f"/api/runs/{run_id}/findings").status_code == 404
    assert other.get("/api/auth/admin/users").status_code == 403
    assert other.get("/api/auth/admin/settings").status_code == 403
    assert other.get("/api/settings").status_code == 403
    assert other.patch("/api/settings", json={"max_iterations": 1}).status_code == 403

    assert client.post(f"/api/runs/{run_id}/execution", json={"run_all": True}).status_code == 202
    assert _wait_run(client, run_id)["status"] in ("completed", "blocked", "failed")
    assert client.post(f"/api/runs/{run_id}/execution", json={"selected_ids": []}).status_code == 400
    assert client.post(f"/api/runs/{run_id}/fix/retry", json={"hint": "keep a fresh list for every call"}).status_code == 202
    assert _wait_run(client, run_id)["status"] in ("completed", "blocked", "failed")
    assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_api_learning_batch_and_account_actions(api_env):
    client, db, app = api_env
    run_id = _make_run(client)
    db.add_fix_memory(run_id, "append_item", "mutable default", "def append_item(item, items=[]): pass",
                      "def append_item(item, items=None): pass", user_id=1)
    memories = client.get("/api/learning")
    assert memories.status_code == 200 and memories.json()["items"]
    fix_id = memories.json()["items"][0]["id"]
    assert client.patch(f"/api/learning/{fix_id}", json={"rating": 1}).status_code == 200
    assert client.patch(f"/api/learning/run/{run_id}", json={"rating": -1}).status_code == 200
    assert client.patch(f"/api/learning/999999", json={"rating": 1}).status_code == 404
    assert client.patch(f"/api/learning/run/missing", json={"rating": 0}).status_code == 404
    assert client.patch(f"/api/learning/{fix_id}", json={"rating": 99}).status_code == 422
    assert client.delete("/api/learning/999999").status_code == 404
    for fmt in ("chat", "prompt"):
        assert client.get(f"/api/learning/export?format={fmt}").status_code == 200
    assert client.delete(f"/api/learning/{fix_id}").status_code == 200
    assert client.delete("/api/learning").status_code == 200

    upload = {"files": ("batch.py", b"def add(a, b):\n    return a+b\n", "text/x-python")}
    assert client.post("/api/batch", files=[]).status_code == 422
    response = client.post("/api/batch", files=upload, data={"requirements": "add(1,2) == 3",
                                                              "options": '{"project":"API batch","max_tests_per_function":3}'})
    assert response.status_code == 202, response.text
    batch_id = response.json()["batch_id"]
    for _ in range(600):
        current = client.get(f"/api/batch/{batch_id}")
        if current.json()["status"] != "running":
            break
        time.sleep(0.05)
    assert current.status_code == 200 and current.json()["status"] == "completed"
    assert client.get(f"/api/batch/{batch_id}/download").status_code == 200

    # Account-level cleanup/logout; endpoint errors keep the same JSON shape.
    assert client.delete("/api/auth/account/runs").status_code == 200
    assert client.post("/api/auth/account/delete", json={"confirm": False}).status_code == 400
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/login", json={"username": "apiadmin", "password": "StrongPass123!"}).status_code == 200
    assert client.post("/api/auth/password", json={"current_password": "StrongPass123!", "new_password": "EvenStronger456!"}).status_code == 200


def test_api_validation_and_login_required(api_env, monkeypatch):
    client, db, _ = api_env
    from database.db import Database
    monkeypatch.setenv("TESTPILOT_REQUIRE_LOGIN", "1")
    anon = TestClient(create_app(Database(db.path.parent / "required.db")), raise_server_exceptions=False)
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.post("/api/runs", json={"code": "def f(): return 1"}).status_code == 401
    assert anon.get("/api/runs").status_code == 401
    assert anon.get("/api/settings/models").status_code == 401
    assert anon.get("/api/settings/availability").status_code == 401
    assert anon.get("/api/learning").status_code == 401
    assert anon.get("/api/samples").status_code == 401
    assert anon.get("/api/samples/sample_buggy").status_code == 401
    assert anon.delete("/api/learning").status_code == 401
    assert anon.delete("/api/auth/account/runs").status_code == 401
    assert client.get("/api/dashboard").status_code == 200
    assert client.post("/api/samples/../README").status_code in (400, 404)
    assert client.get("/api/learning/export?format=bad").status_code == 400
    assert client.patch("/api/settings", json={"execution_mode": "unsafe"}).status_code == 400


def test_every_openapi_operation_has_a_negative_request_case(api_env, monkeypatch):
    client, _, app = api_env
    monkeypatch.setenv("TESTPILOT_REQUIRE_LOGIN", "1")
    anonymous = TestClient(app, raise_server_exceptions=False)
    paths = anonymous.get("/openapi.json").json()["paths"]
    exempt = {("GET", "/api/health"), ("POST", "/api/auth/logout")}
    for path, operations in paths.items():
        concrete = re.sub(r"\{user_id\}", "999999", path)
        concrete = re.sub(r"\{fix_id\}", "999999", concrete)
        concrete = re.sub(r"\{[^}]+\}", "missing-item", concrete)
        for method in operations:
            key = (method.upper(), path)
            if key in exempt:
                continue
            response = anonymous.request(method.upper(), concrete)
            assert response.status_code >= 400, f"Expected a negative case for {method.upper()} {path}, got {response.status_code}"
    assert anonymous.post("/api/health").status_code == 405
    assert anonymous.get("/api/auth/logout").status_code == 405
