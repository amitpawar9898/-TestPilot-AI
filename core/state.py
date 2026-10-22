"""Shared run state passed between agents."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

WAITING, RUNNING, COMPLETED, FAILED, BLOCKED, NEEDS_REVIEW = "waiting", "running", "completed", "failed", "blocked", "needs_review"
AGENT_ORDER = [
    ("planner", "Planner", "Turns the goal into an execution plan"),
    ("code_analysis", "Code Analysis", "Parses code, finds structure and static issues"),
    ("test_generation", "Test Generation", "Designs test cases and pytest code"),
    ("test_execution", "Test Execution", "Runs tests in an isolated environment"),
    ("critic", "Critic & Validation", "Judges evidence and test quality"),
    ("fix", "Fix", "Proposes a patch and proves it with the same tests"),
    ("improve", "Improve", "Applies measurable, behavior-preserving quality edits"),
    ("reporting", "Reporting", "Merges findings and saves the report"),
]
AGENT_NAMES = {k: n for k, n, _ in AGENT_ORDER}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class ToolCall:
    name: str
    summary: str
    result: str
    ok: bool
    duration_ms: int


@dataclass
class AgentRecord:
    key: str
    name: str
    status: str = WAITING
    task: str = ""
    input_summary: str = ""
    output_summary: str = ""
    reason: str = ""
    iteration: int = 1
    tool_calls: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0


@dataclass
class RunRequest:
    code: str
    filename: str = "submitted.py"
    objective: str = "Find functional bugs and edge-case failures"
    requirements: str = ""
    targets: list = field(default_factory=list)
    project: str = "Default project"
    categories: list = field(default_factory=lambda: ["normal", "boundary", "edge", "negative"])
    max_tests_per_function: int = 14
    pause_for_review: bool = False
    auto_fix: bool = True
    user_id: int | None = None  # owner when login is used; None = anonymous
    fix_mode: str = "safe"  # safe = free deterministic patches + confirmed failures; ai = LLM fixes in verified rounds
    improve_code: bool = True


@dataclass
class RunState:
    request: RunRequest
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "created"  # created|running|awaiting_review|completed|blocked|failed
    verdict: str = ""
    stop_reason: str = ""
    iteration: int = 1
    max_iterations: int = 2
    plan: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    cases: list = field(default_factory=list)
    results: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    critic: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    agents: dict = field(default_factory=dict)  # key -> AgentRecord (current iteration)
    history: list = field(default_factory=list)  # superseded AgentRecords from earlier iterations
    extra_done: list = field(default_factory=list)
    review_done: bool = False
    fix: dict = field(default_factory=dict)
    improvement: dict = field(default_factory=dict)
    fix_hint: str = ""
    fix_retry: bool = False
    fix_after_results: list = field(default_factory=list)
    isolation: str = ""
    sandbox: dict = field(default_factory=dict)
    llm_used: bool = False
    gen_warnings: list = field(default_factory=list)
    created_at: str = field(default_factory=now)
    finished_at: str | None = None

    def agent_dicts(self) -> list[dict]:
        return [asdict(self.agents[k]) for k, _, _ in AGENT_ORDER if k in self.agents]

    def result_for(self, case_id: str) -> dict | None:
        return next((r for r in self.results if r["case_id"] == case_id), None)
