from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from config.settings import Settings
from core.batch import run_batch
from core.state import RunRequest


class Runtime:
    """In-process workers keep the existing synchronous orchestrator off request threads."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="testpilot")
        self.lock = threading.RLock()
        self.states: dict[str, object] = {}
        self.jobs: dict[str, dict] = {}
        self.batches: dict[str, dict] = {}

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=False)

    @staticmethod
    def _mark_failed(db, run_id: str) -> None:
        try:
            if db.get_run(run_id):
                from database.db import now_iso
                db.update_run(run_id, status="failed", stop_reason="Background run failed unexpectedly.", finished_at=now_iso())
        except Exception:
            pass

    def start_run(self, db, settings: Settings, request: RunRequest, run_id: str) -> None:
        with self.lock:
            self.jobs[run_id] = {"status": "running", "error": "", "owner": request.user_id}
        def work():
            try:
                from core.orchestrator import Orchestrator
                def publish(state):
                    with self.lock:
                        self.states[run_id] = state
                state = Orchestrator(settings, db, on_update=publish).start(request, run_id=run_id)
                with self.lock:
                    self.states[run_id] = state
                    self.jobs[run_id] = {"status": state.status, "error": ""}
            except Exception as exc:
                self._mark_failed(db, run_id)
                with self.lock:
                    self.jobs[run_id] = {"status": "failed", "error": type(exc).__name__}
        self.pool.submit(work)

    def continue_run(self, db, settings: Settings, run_id: str, selected_ids: list[str] | None) -> None:
        with self.lock:
            state = self.states.get(run_id)
            if not state:
                raise ValueError("This run is no longer available to resume in this server session.")
            if self.jobs.get(run_id, {}).get("status") == "running":
                raise ValueError("This run is already being processed.")
            self.jobs[run_id] = {"status": "running", "error": ""}
        def work():
            try:
                from core.orchestrator import Orchestrator
                out = Orchestrator(settings, db).continue_run(state, selected_ids)
                with self.lock:
                    self.states[run_id] = out
                    self.jobs[run_id] = {"status": out.status, "error": ""}
            except Exception as exc:
                self._mark_failed(db, run_id)
                with self.lock:
                    self.jobs[run_id] = {"status": "failed", "error": type(exc).__name__}
        self.pool.submit(work)

    def retry_fix(self, db, settings: Settings, run_id: str, hint: str) -> None:
        with self.lock:
            state = self.states.get(run_id)
            if not state:
                raise ValueError("The source is not available in this server session.")
            if self.jobs.get(run_id, {}).get("status") == "running":
                raise ValueError("This run is already being processed.")
            self.jobs[run_id] = {"status": "running", "error": ""}
        def work():
            try:
                from core.orchestrator import Orchestrator
                out = Orchestrator(settings, db).retry_fix(state, hint)
                with self.lock:
                    self.states[run_id] = out
                    self.jobs[run_id] = {"status": out.status, "error": ""}
            except Exception as exc:
                self._mark_failed(db, run_id)
                with self.lock:
                    self.jobs[run_id] = {"status": "failed", "error": type(exc).__name__}
        self.pool.submit(work)

    def start_batch(self, db, settings: Settings, files: list[tuple[str, str, str]], template: RunRequest,
                    shared_requirements: str, batch_id: str) -> None:
        with self.lock:
            self.batches[batch_id] = {"status": "running", "states": [], "owner": template.user_id}
        def work():
            try:
                from core.orchestrator import Orchestrator
                states = run_batch(Orchestrator(settings, db), files, template, shared_requirements)
                with self.lock:
                    for state in states:
                        self.states[state.run_id] = state
                        self.jobs[state.run_id] = {"status": state.status, "error": "", "owner": template.user_id}
                    self.batches[batch_id] = {"status": "completed", "states": states, "owner": template.user_id}
            except Exception as exc:
                with self.lock:
                    self.batches[batch_id] = {"status": "failed", "states": [], "owner": template.user_id,
                                              "error": type(exc).__name__}
        self.pool.submit(work)

    def state_for(self, run_id: str):
        with self.lock:
            return self.states.get(run_id)

    def job_for(self, run_id: str):
        with self.lock:
            return self.jobs.get(run_id)

    def batch_for(self, batch_id: str):
        with self.lock:
            return self.batches.get(batch_id)
