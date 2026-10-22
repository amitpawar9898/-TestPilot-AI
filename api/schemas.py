from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(APIModel):
    detail: str
    code: str


class User(APIModel):
    id: int
    username: str
    role: Literal["user", "admin"]


class AuthRequest(APIModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(AuthRequest):
    pass


class LoginRequest(AuthRequest):
    pass


class AuthResponse(APIModel):
    user: User
    authenticated: bool = True


class MessageResponse(APIModel):
    message: str


class CountResponse(APIModel):
    deleted: int = 0
    scrubbed: int = 0


class PasswordChangeRequest(APIModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class AdminCreateUserRequest(AuthRequest):
    role: Literal["user", "admin"] = "user"


class RoleUpdateRequest(APIModel):
    role: Literal["user", "admin"]


class AccessSettings(APIModel):
    require_login: bool
    allow_signup: bool


class ConfirmRequest(APIModel):
    confirm: bool


class Health(APIModel):
    status: str


class RunCreateRequest(APIModel):
    code: str = Field(min_length=1, max_length=200_000)
    filename: str = Field(default="submitted.py", max_length=255)
    objective: str = Field(default="Find functional bugs and edge-case failures", max_length=1000)
    requirements: str = Field(default="", max_length=50_000)
    targets: list[str] = Field(default_factory=list, max_length=200)
    project: str = Field(default="Default project", max_length=80)
    categories: list[str] = Field(default_factory=lambda: ["normal", "boundary", "edge", "negative"])
    max_tests_per_function: int = Field(default=14, ge=3, le=60)
    pause_for_review: bool = False
    auto_fix: bool = True
    fix_mode: Literal["safe", "ai"] = "safe"
    improve_code: bool = True


class RunAccepted(APIModel):
    run_id: str
    status: str


class RunSummary(APIModel):
    id: str
    created_at: str
    finished_at: str | None = None
    project: str
    filename: str | None = None
    status: str
    verdict: str = ""
    tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    findings: int = 0
    iterations: int = 1


class RunList(APIModel):
    items: list[RunSummary]


class RunStatus(APIModel):
    run_id: str
    status: str
    iteration: int
    max_iterations: int
    stop_reason: str = ""
    finished_at: str | None = None
    fix: dict[str, Any] | None = None
    improvement: dict[str, Any] | None = None
    current_stage: str = ""
    current_task: str = ""
    completed_stages: int = 0
    total_stages: int = 8
    progress_percent: int = 0
    elapsed_seconds: int = 0
    tests_processed: int = 0
    tests_total: int = 0


class ToolCall(APIModel):
    name: str
    summary: str
    result: str
    ok: bool
    duration_ms: int


class AgentEvent(APIModel):
    id: int | None = None
    run_id: str
    agent: str
    iteration: int = 1
    status: str
    task: str = ""
    input_summary: str = ""
    output_summary: str = ""
    reason: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0


class AgentEvents(APIModel):
    items: list[AgentEvent]


class AnalysisPreviewRequest(APIModel):
    code: str = Field(min_length=1, max_length=200_000)
    filename: str = Field(default="submitted.py", max_length=255)


class AnalysisResponse(APIModel):
    ok: bool
    syntax_error: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    functions: list[dict[str, Any]] = Field(default_factory=list)
    classes: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    third_party: list[str] = Field(default_factory=list)
    ruff: dict[str, Any] = Field(default_factory=dict)
    imports: list[dict[str, Any]] = Field(default_factory=list)
    module_exceptions: list[str] = Field(default_factory=list)
    script_mode: bool = False


class Finding(APIModel):
    id: int | None = None
    run_id: str | None = None
    run_created: str | None = None
    finding_id: str
    category: str
    title: str | None = None
    description: str | None = None
    severity: str = "Low"
    file: str | None = None
    line: int | None = None
    function: str | None = None
    rule: str | None = None
    evidence: str | None = None
    expected: str | None = None
    actual: str | None = None
    suggested_fix: str | None = None
    validation_status: str | None = None
    source_tool: str | None = None
    case_ids: list[str] = Field(default_factory=list)


class FindingList(APIModel):
    items: list[Finding]


class TestResult(APIModel):
    id: int | None = None
    run_id: str | None = None
    case_id: str
    status: str
    actual: str = ""
    detail: str = ""
    exception_type: str | None = None
    exception_message: str | None = None
    traceback: str = ""
    stdout: str = ""
    line: int | None = None
    duration_ms: float = 0
    isolation: str = ""
    executed_at: str | None = None


class TestCase(APIModel):
    id: str
    iteration: int = 1
    name: str
    category: str
    function: str
    args: str = "()"
    kwargs: str = "{}"
    ctor: str | None = None
    setup: list[dict[str, Any]] | None = None
    expect: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    source: str = ""
    oracle: str = ""
    enabled: bool = True
    result: TestResult | None = None


class TestCaseList(APIModel):
    items: list[TestCase]


class TestCaseCreateRequest(APIModel):
    function: str
    name: str = Field(min_length=1, max_length=200)
    args: str = "()"
    kwargs: str = "{}"
    expected: str = "no exception"
    ctor: str | None = None
    setup: list[dict[str, Any]] | None = None


class TestCaseEdit(APIModel):
    id: str
    enabled: bool
    name: str
    args: str
    kwargs: str
    ctor: str | None = None
    expected: str


class TestCaseEditsRequest(APIModel):
    items: list[TestCaseEdit]


class ExecutionRequest(APIModel):
    selected_ids: list[str] | None = None
    run_all: bool = False


class FixResponse(APIModel):
    status: str = "pending"
    mode: str = "safe"
    diff: str = ""
    patched_source: str | None = None
    syntax_repair: dict[str, Any] | None = None
    applied: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    regressions: list[dict[str, Any]] = Field(default_factory=list)
    fixed_cases: list[str] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    hint: str = ""
    ai_rounds: int = 0
    memory_used: int = 0
    memory_saved: int = 0
    static_before: int | None = None
    static_after: int | None = None
    improvement: dict[str, Any] | None = None


class FixRetryRequest(APIModel):
    hint: str = Field(default="", max_length=2000)


class FilePayload(APIModel):
    filename: str
    media_type: str
    content_base64: str


class ReportResponse(APIModel):
    meta: dict[str, Any]
    verdict: str = ""
    summary: dict[str, Any]
    scope: dict[str, Any] = Field(default_factory=dict)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    critic: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    fix: dict[str, Any] = Field(default_factory=dict)
    improvement: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    stop_reason: str = ""


class RunDetail(APIModel):
    run: dict[str, Any]
    analysis: AnalysisResponse | None = None
    tests: list[TestCase] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    report: ReportResponse | None = None
    fix: FixResponse | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    plan: dict[str, Any] | None = None


class DashboardResponse(APIModel):
    metrics: dict[str, Any]
    trend: list[dict[str, Any]]
    latest_runs: list[RunSummary]
    latest_events: list[dict[str, Any]]
    latest_findings: list[dict[str, Any]]
    timeline: list[dict[str, Any]]


class LearningRatingRequest(APIModel):
    rating: Literal[-1, 0, 1]


class LearningList(APIModel):
    items: list[dict[str, Any]]


class SettingsResponse(APIModel):
    settings: dict[str, Any]
    llm_ready: bool
    llm_reason: str


class SettingsUpdateRequest(APIModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    llm_consent: bool | None = None
    execution_mode: str | None = None
    docker_image: str | None = None
    per_test_timeout: int | None = Field(default=None, ge=1, le=60)
    total_timeout: int | None = Field(default=None, ge=5, le=600)
    memory_mb: int | None = Field(default=None, ge=64, le=4096)
    max_iterations: int | None = Field(default=None, ge=1, le=5)
    max_tests_per_function: int | None = Field(default=None, ge=3, le=60)
    retain_source: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    require_login: bool | None = None
    allow_signup: bool | None = None
    learn_from_fixes: bool | None = None
    use_fix_memory: bool | None = None
    report_include_tests: bool | None = None
    report_include_ai_notes: bool | None = None


class PurgeRequest(APIModel):
    days: int = Field(ge=1, le=3650)


class ConnectionTestRequest(APIModel):
    provider: str | None = None
    model: str | None = None


class ConnectionTestResponse(APIModel):
    ok: bool
    message: str


class Availability(APIModel):
    docker: dict[str, Any]
    ollama: dict[str, Any]


class ModelList(APIModel):
    providers: list[dict[str, Any]]
    models: list[dict[str, Any]]
    hints: dict[str, str]


class SampleFile(APIModel):
    name: str
    code_name: str
    requirements_name: str | None = None


class SampleList(APIModel):
    items: list[SampleFile]


class SampleContent(APIModel):
    filename: str
    code: str
    requirements: str


class BatchAccepted(APIModel):
    batch_id: str
    status: str
    files: list[str]
    warnings: list[str] = Field(default_factory=list)


class BatchOptions(APIModel):
    objective: str = "Find bugs"
    project: str = "Default project"
    categories: list[str] = Field(default_factory=lambda: ["normal", "boundary", "edge", "negative"])
    max_tests_per_function: int = Field(default=14, ge=3, le=60)
    auto_fix: bool = True
    fix_mode: Literal["safe", "ai"] = "safe"


class BatchStatus(APIModel):
    batch_id: str
    status: str
    runs: list[RunSummary]
    summary: list[dict[str, Any]] = Field(default_factory=list)
