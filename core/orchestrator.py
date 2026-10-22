"""Central orchestrator: runs the agents in order, applies conditional transitions, records every step."""
from __future__ import annotations

import time
from dataclasses import asdict

from agents.base import AgentContext, AgentFailure
from agents.code_analysis_agent import CodeAnalysisAgent
from agents.critic_agent import CriticAgent
from agents.execution_agent import TestExecutionAgent
from agents.fix_agent import FixAgent
from agents.improve_agent import ImproveAgent
from agents.planner_agent import PlannerAgent
from agents.reporting_agent import ReportingAgent
from agents.test_generation_agent import TestGenerationAgent
from config.settings import Settings
from core.state import AGENT_NAMES, AGENT_ORDER, BLOCKED, COMPLETED, FAILED, RUNNING, WAITING, AgentRecord, RunRequest, RunState, now
from core.workflow import PAUSE, next_stage
from database.db import Database
from tools.llm_client import LLMClient

AGENTS = {a.key: a for a in (PlannerAgent(), CodeAnalysisAgent(), TestGenerationAgent(), TestExecutionAgent(), CriticAgent(), FixAgent(), ImproveAgent(), ReportingAgent())}


class Orchestrator:
    def __init__(self, settings: Settings, db: Database, on_update=None):
        self.settings, self.db, self.on_update = settings, db, on_update
        self.llm = LLMClient.from_settings(settings)

    # ------------------------------------------------------------ public API
    def start(self, request: RunRequest, run_id: str | None = None) -> RunState:
        s = RunState(request=request, max_iterations=self.settings.max_iterations)
        if run_id:
            s.run_id = run_id
        s.status = "running"
        s.agents = {k: AgentRecord(k, n) for k, n, _ in AGENT_ORDER}
        model = self.settings.effective_model() if self.settings.llm_status()[0] else ""
        self.db.create_run(s.run_id, request.project, request.objective, request.requirements, request.targets,
                           {"categories": request.categories, "max_tests": request.max_tests_per_function, "pause": request.pause_for_review},
                           self.settings.provider if model else "none", model, user_id=request.user_id)
        self._notify(s)
        return self._drive(s, "planner")

    def continue_run(self, state: RunState, selected_ids: list[str] | None = None) -> RunState:
        """Resume after review, or re-run selected tests of a finished run."""
        s = state
        if selected_ids is not None:
            for c in s.cases:
                c["enabled"] = c["id"] in selected_ids
        s.review_done = True
        s.status, s.stop_reason, s.finished_at = "running", "", None
        redo = {c["id"] for c in s.cases if c.get("enabled", True)}
        if s.agents["test_execution"].status != WAITING:  # a re-run: drop old results of the chosen cases
            s.results = [r for r in s.results if r["case_id"] not in redo]
        for k in ("test_execution", "critic", "fix", "reporting"):
            self._archive(s, k)
        s.fix = {}
        return self._drive(s, "test_execution")

    def retry_fix(self, state: RunState, hint: str = "") -> RunState:
        """Try again on issues the first fix did not resolve. The hint is passed to the AI along with what was already tried."""
        s = state
        s.fix_hint, s.fix_retry = hint.strip(), True
        s.status, s.stop_reason, s.finished_at = "running", "", None
        for k in ("fix", "improve", "reporting"):
            self._archive(s, k)
        self._run_agent(s, "fix")
        if s.request.improve_code and not s.fix.get("regressions") and s.agents["fix"].status in (COMPLETED, "needs_review"):
            self._run_agent(s, "improve")
        else:
            reason = "Code improvement was turned off for this run." if not s.request.improve_code else "Improvement skipped because the fix stage did not pass."
            s.agents["improve"].status, s.agents["improve"].reason = BLOCKED, reason
            s.improvement = {"status": "skipped", "reason": reason}
        self._run_agent(s, "reporting")
        s.fix_retry = False
        self._finish(s, 0.0)
        return s

    # ------------------------------------------------------------- internals
    def _archive(self, s: RunState, key: str) -> None:
        rec = s.agents.get(key)
        if rec and rec.status != WAITING:
            s.history.append(rec)
        s.agents[key] = AgentRecord(key, AGENT_NAMES[key], iteration=s.iteration)

    def _drive(self, s: RunState, key: str | None) -> RunState:
        t0 = time.perf_counter()
        while key and key != PAUSE:
            if key == "planner" and s.agents["planner"].status != WAITING:  # the Critic asked for another bounded cycle
                s.iteration += 1
                for k in ("planner", "test_generation", "test_execution", "critic"):
                    self._archive(s, k)
            self._run_agent(s, key)
            key = next_stage(s, key)
            if key is None or key == PAUSE:
                break
        if key == PAUSE:
            s.status = "awaiting_review"
            s.agents["test_execution"].status = WAITING
            s.agents["test_generation"].status = "needs_review"
            s.agents["test_generation"].reason += " Waiting for your review: edit or deselect cases, then run them."
            self._persist(s)
            self._notify(s)
            return s
        self._finish(s, time.perf_counter() - t0)
        return s

    def _run_agent(self, s: RunState, key: str) -> None:
        rec = s.agents[key]
        rec.status, rec.started_at, rec.iteration = RUNNING, now(), s.iteration
        rec.warnings, rec.errors, rec.tool_calls = [], [], []
        self._notify(s)
        ctx = AgentContext(s, rec, self.settings, self.db, self.llm, notify=lambda: self._notify(s))
        t0 = time.perf_counter()
        try:
            AGENTS[key].run(ctx)
            if rec.status == RUNNING:
                rec.status = COMPLETED
        except AgentFailure as e:
            rec.status, rec.reason = FAILED, str(e)
            rec.errors.append(str(e))
        except Exception as e:  # noqa: BLE001 - an agent crash must not take the app down
            rec.status = FAILED
            rec.reason = f"Unexpected {type(e).__name__}: {str(e)[:200]}"
            rec.errors.append(rec.reason)
        rec.ended_at = now()
        rec.duration_ms = int((time.perf_counter() - t0) * 1000)
        self._persist(s, event=rec)
        self._notify(s)

    def _finish(self, s: RunState, elapsed: float) -> None:
        reason = s.stop_reason or "Run ended before this stage."
        for k, rec in s.agents.items():
            if rec.status == WAITING:
                rec.status, rec.reason = BLOCKED, reason
                self.db.add_event(s.run_id, asdict(rec))
        if s.status in ("running", "created"):
            s.status = "failed" if s.agents["reporting"].status == FAILED or s.agents["planner"].status == FAILED else "completed"
        if s.agents["test_execution"].status == BLOCKED and s.status == "completed":
            s.status = "blocked"
            s.stop_reason = s.agents["test_execution"].reason
        s.finished_at = now()
        self._persist(s)
        self._notify(s)

    def _persist(self, s: RunState, event: AgentRecord | None = None) -> None:
        db = self.db
        if event is not None:
            db.add_event(s.run_id, asdict(event))
        if s.analysis:
            m = dict(s.analysis.get("meta", {}))
            m.setdefault("filename", s.request.filename)
            db.save_source(s.run_id, m, s.analysis, s.request.code if self.settings.retain_source else None)
        if s.improvement:
            db.save_improvement(s.run_id, s.improvement)
        db.replace_cases(s.run_id, s.cases)
        db.replace_results(s.run_id, s.results)
        db.update_run(s.run_id, status=s.status, verdict=s.verdict, iterations=s.iteration, isolation=s.isolation,
                      stop_reason=s.stop_reason[:500], finished_at=s.finished_at,
                      llm_provider=self.settings.provider if s.llm_used else "none", llm_model=self.settings.effective_model() if s.llm_used else "")

    def _notify(self, s: RunState) -> None:
        if self.on_update:
            try:
                self.on_update(s)
            except Exception:  # noqa: BLE001 - UI problems must not break the workflow
                pass
