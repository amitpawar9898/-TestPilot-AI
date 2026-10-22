"""Isolated test execution.

Security model
--------------
* docker:        disposable container, --network none, memory/CPU/pids limits, read-only root FS, all capabilities
                 dropped, non-root user, source mounted read-only. This is the only mode intended for untrusted code.
* local_trusted: a plain subprocess with a scrubbed environment and POSIX rlimits. It is NOT a sandbox. It requires the
                 explicit opt-in TESTPILOT_ALLOW_UNSAFE_LOCAL=1 and exists for the user's own trusted code.
* disabled:      nothing is executed.
If the selected mode is unavailable, no code is executed and no test is ever reported as passed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from config.settings import Settings, unsafe_local_allowed

HARNESS = Path(__file__).with_name("harness.py")
MARK = "@@TP@@"
_docker_cache: dict = {"t": 0.0, "ok": False, "why": ""}


def _docker_ready() -> tuple[bool, str]:
    if time.time() - _docker_cache["t"] < 30:
        return _docker_cache["ok"], _docker_cache["why"]
    ok, why = False, ""
    if not shutil.which("docker"):
        why = "Docker CLI not found. Install Docker Desktop and make sure `docker` is on PATH."
    else:
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=8)
            ok = r.returncode == 0
            why = "" if ok else "Docker is installed but the daemon is not running. Start Docker Desktop."
        except Exception as e:  # noqa: BLE001
            why = f"Docker check failed: {type(e).__name__}."
    _docker_cache.update(t=time.time(), ok=ok, why=why)
    return ok, why


def sandbox_status(settings: Settings) -> dict:
    mode = settings.execution_mode
    if mode == "disabled":
        return {"mode": mode, "available": False, "secure": False, "label": "disabled",
                "reason": "Execution is disabled in Settings. Tests are generated but never run."}
    if mode == "docker":
        ok, why = _docker_ready()
        return {"mode": mode, "available": ok, "secure": ok, "reason": why,
                "label": f"docker ({settings.docker_image}, network=none, {settings.memory_mb}MB, pids<=64, read-only FS)"}
    if not unsafe_local_allowed():
        return {"mode": mode, "available": False, "secure": False, "label": "local-trusted (not enabled)",
                "reason": "Local execution is not a sandbox and is disabled. Set TESTPILOT_ALLOW_UNSAFE_LOCAL=1 in .env to run YOUR OWN trusted code."}
    return {"mode": mode, "available": True, "secure": False, "reason": "",
            "label": "local-trusted process (NOT a sandbox: no filesystem/network isolation)"}


def _text(x) -> str:
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")


def run_cases(source: str, cases: list[dict], settings: Settings, status: dict | None = None) -> dict:
    status = status or sandbox_status(settings)
    base = {"isolation": status["label"], "results": [], "ok": False, "available": status["available"], "error": None, "duration_ms": 0}
    if not status["available"]:
        return {**base, "error": status["reason"]}
    runnable = [{"id": c["id"], "function": c["function"], "args": c.get("args") or "()", "kwargs": c.get("kwargs") or "{}", "ctor": c.get("ctor") or "()", "setup": c.get("setup") or [],
                 "expect": c["expect"]} for c in cases]
    t0 = time.perf_counter()
    name = f"testpilot-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="testpilot_") as td:
        work = Path(td)
        (work / "user_module.py").write_text(source, encoding="utf-8")
        shutil.copy(HARNESS, work / "harness.py")
        (work / "cases.json").write_text(json.dumps(runnable), encoding="utf-8")
        if os.name == "posix":
            os.chmod(work, 0o755)
            for f in work.iterdir():
                os.chmod(f, 0o644)
        per, mem = str(settings.per_test_timeout), settings.memory_mb
        total = settings.total_timeout
        if status["mode"] == "docker":
            cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "--memory", f"{mem}m", "--memory-swap", f"{mem}m",
                   "--cpus", "1", "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp:rw,size=16m", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "65534:65534",
                   "--mount", f"type=bind,source={work},target=/work,readonly", "-w", "/work",
                   "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp", settings.docker_image,
                   "python", "-I", "-B", "/work/harness.py", "/work/user_module.py", "/work/cases.json", per, "0"]
            env, cwd, total = None, None, total + 25  # allow time to start (and possibly pull) the container
        else:
            cmd = [sys.executable, "-I", "-B", str(work / "harness.py"), str(work / "user_module.py"), str(work / "cases.json"), per, str(mem * 4)]
            env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG")}  # no API keys leak in
            env["PYTHONIOENCODING"] = "utf-8"
            cwd = str(work)
        timed_out = False
        try:
            cp = subprocess.run(cmd, capture_output=True, timeout=total, env=env, cwd=cwd)
            out, err = _text(cp.stdout), _text(cp.stderr)
        except subprocess.TimeoutExpired as e:
            timed_out, out, err = True, _text(e.stdout), _text(e.stderr)
            if status["mode"] == "docker":
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20)
        except Exception as e:  # noqa: BLE001
            return {**base, "error": f"Could not start the runner: {type(e).__name__}: {e}", "duration_ms": int((time.perf_counter() - t0) * 1000)}
    started, got, done = None, {}, False
    for line in out.splitlines():
        if not line.startswith(MARK):
            continue
        try:
            msg = json.loads(line[len(MARK):])
        except ValueError:
            continue
        if msg.get("type") == "start":
            started = msg["id"]
        elif msg.get("type") == "result":
            got[msg["id"]] = msg
        elif msg.get("type") == "done":
            done = True
    results = []
    for c in cases:
        r = got.get(c["id"])
        if r is None:
            if timed_out and c["id"] == started:
                r = {"status": "timeout", "detail": f"Run exceeded the {settings.total_timeout}s total limit while this test was running (possible infinite loop)."}
            else:
                r = {"status": "not_run", "detail": "Not executed: the run ended before reaching this test."}
        r = {k: v for k, v in r.items() if k not in ("type", "id")}
        results.append({"case_id": c["id"], "isolation": status["label"], **r})
    problem = None
    if timed_out:
        problem = f"Total execution limit of {settings.total_timeout}s reached; unfinished tests are marked timeout/not_run."
    elif not done:
        problem = (err.strip().splitlines() or ["Runner exited without producing results."])[-1][:300]
    return {**base, "results": results, "ok": done, "error": problem, "duration_ms": int((time.perf_counter() - t0) * 1000), "stderr": err[-500:]}
