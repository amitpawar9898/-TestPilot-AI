# API contract (Phase 1)

All paths are under `/api`. JSON bodies and JSON responses use explicit Pydantic schemas. Download bodies are `FilePayload { filename: str, media_type: str, content_base64: str }`. Shared errors use `{ "detail": "human-readable message", "code": "..." }`; expected codes: `400` invalid action/data, `401` unauthenticated or expired, `403` role/ownership denied, `404` missing resource, `409` conflict (duplicate username or wrong run state), `413` upload too large, `422` schema validation, `500` generic internal error (no stack or secret). 

Authentication is required when `login_required(Settings.load())` is true. When optional, anonymous data continues to use `user_id=None`; a signed-in user only accesses their own records. Admin routes always require an authenticated admin. The cookie lasts eight hours and is revocable at logout; bearer header is supported for non-browser clients.

## Endpoints

| Method | Path | Auth | Request schema / query | Response schema | Errors | Replaces / backing functionality |
|---|---|---:|---|---|---|---|
| GET | `/health` | no | none | `Health {status: str}` | 500 | NEW; process health, no domain operation |
| POST | `/auth/register` | no | `RegisterRequest {username, password}` | `AuthResponse {user: User, authenticated: bool}`; sets session cookie | 400, 409, 422 | Account create form; `core.auth.register`, `Database.count_users/create_user/get_user` |
| POST | `/auth/login` | no | `LoginRequest {username, password}` | `AuthResponse`; sets session cookie | 401, 422 | Sign-in form; `core.auth.authenticate` |
| POST | `/auth/logout` | optional | none | `MessageResponse {message}`; clears/revokes token | 401 | Sign out; NEW token revocation adapter |
| GET | `/auth/me` | conditional | none | `User | null` | 401 | Account identity / current user; NEW session adapter |
| POST | `/auth/password` | yes | `PasswordChangeRequest {current_password, new_password}` | `MessageResponse` | 400, 401, 422 | Change password; `core.auth.change_password` |
| GET | `/auth/admin/users` | admin | none | `User[]` | 401, 403 | User administration; `Database.list_users` |
| POST | `/auth/admin/users` | admin | `AdminCreateUserRequest {username, password, role}` | `User` | 400, 401, 403, 409, 422 | Add user; `core.auth.register(by_admin=True)` |
| PATCH | `/auth/admin/users/{user_id}` | admin | `RoleUpdateRequest {role}` | `User` | 401, 403, 404, 422 | Change user role; `Database.set_role` |
| DELETE | `/auth/admin/users/{user_id}` | admin | none | `MessageResponse` | 401, 403, 404 | Delete user and owned data; `Database.delete_user` |
| GET | `/auth/admin/settings` | admin | none | `AccessSettings {require_login, allow_signup}` | 401, 403 | Admin access checkboxes; `Settings.load/login_required` |
| PATCH | `/auth/admin/settings` | admin | `AccessSettings` | `AccessSettings` | 401, 403, 422 | Save access settings; `Settings.save` |
| POST | `/auth/account/delete` | yes | `ConfirmRequest {confirm: bool}` | `MessageResponse` | 401, 403, 422 | Delete own account/data; `Database.delete_user` (last-admin check retained) |
| DELETE | `/auth/account/runs` | yes/conditional | none | `CountResponse {deleted: int}` | 401 | Delete own runs; `Database.purge_older_than(0, user_id=...)` |
| GET | `/dashboard` | conditional | none | `DashboardResponse` (metrics, trend, latest runs/events/findings) | 401 | Dashboard; `Database.metrics/runs_trend/list_runs/timeline/recent_events/get_findings` |
| POST | `/runs` | conditional | `RunCreateRequest` (code, filename, objective, requirements, targets, project, categories, max_tests_per_function, pause_for_review, auto_fix, fix_mode, improve_code) | `RunAccepted {run_id, status}` | 401, 413, 422 | Studio single-run action; `Orchestrator.start` in background worker |
| GET | `/runs` | conditional | `search?, status?, verdict?, since?, limit?` | `RunList {items: RunSummary[]}` | 401, 422 | History/dashboard; `Database.list_runs` with user filter |
| GET | `/runs/{run_id}` | conditional | none | `RunDetail` | 401, 403, 404 | Open run/history; `Database.get_run`, plus persisted cases/results/report/analysis |
| GET | `/runs/{run_id}/status` | conditional | none | `RunStatus {run_id, status, iteration, max_iterations, stop_reason, finished_at}` | 401, 403, 404 | Studio/monitor polling; NEW adapter over worker state and `Database.get_run` |
| GET | `/runs/{run_id}/events` | conditional | `after_id?` | `AgentEvents {items: AgentEvent[]}` | 401, 403, 404 | Workflow monitor; `Database.get_events` |
| GET | `/runs/{run_id}/analysis` | conditional | none | `AnalysisResponse` | 401, 403, 404 | Studio quick look / Quality; `Database.get_analysis` |
| GET | `/runs/{run_id}/findings` | conditional | none | `FindingList {items: Finding[]}` | 401, 403, 404 | Bug Reports; `Database.get_findings` |
| GET | `/runs/{run_id}/tests` | conditional | none | `TestCaseList {items: TestCase[]}` | 401, 403, 404 | Test Generator; `Database.get_cases` and `get_results` |
| GET | `/runs/{run_id}/tests/download` | conditional | `format: pytest\|csv` | `FilePayload` | 400, 401, 403, 404 | Download pytest file or test-results CSV; `tools.test_generator.export_pytest`, `tools.report_generator.tests_csv` |
| POST | `/runs/{run_id}/tests` | conditional | `TestCaseCreateRequest` | `TestCase` | 400, 401, 403, 404, 409, 422 | Add test case; `tools.test_generator.parse_expect/validate_case`; append to in-memory state and persist cases (NEW adapter) |
| PATCH | `/runs/{run_id}/tests` | conditional | `TestCaseEditsRequest {items: TestCaseEdit[]}` | `TestCaseList` | 400, 401, 403, 404, 409, 422 | Apply edits / enable selection; existing case validation and `Database.replace_cases` (NEW adapter) |
| POST | `/runs/{run_id}/execution` | conditional | `ExecutionRequest {selected_ids?: str[], run_all: bool}` | `RunAccepted {run_id, status}` | 400, 401, 403, 404, 409, 422 | Run selected/all tests; `Orchestrator.continue_run` background wrapper |
| GET | `/runs/{run_id}/fix` | conditional | none | `FixResponse` | 401, 403, 404 | Auto-Fix review; `RunState.fix` or `Database.get_report` |
| GET | `/runs/{run_id}/fix/diff` | conditional | none | `FilePayload` | 401, 403, 404 | View/download diff; stored fix diff |
| GET | `/runs/{run_id}/fix/download` | conditional | `format: fixed\|diff` | `FilePayload` | 400, 401, 403, 404 | Download fixed source/diff; current run state fix payload |
| GET | `/runs/{run_id}/improvement/download` | conditional | `format: improved\|fixed_diff\|improved_diff` | `FilePayload` | 401, 403, 404, 422 | Download improved source or either pipeline diff |
| POST | `/runs/{run_id}/fix/retry` | conditional | `FixRetryRequest {hint: str}` | `RunAccepted {run_id, status}` | 401, 403, 404, 409, 422 | Fix remaining issues; `Orchestrator.retry_fix` background wrapper |
| GET | `/runs/{run_id}/report` | conditional | none | `ReportResponse` | 401, 403, 404 | Report preview; `Database.get_report` |
| GET | `/runs/{run_id}/report/download` | conditional | `format: json\|html\|findings_csv\|tests_csv` | `FilePayload` | 400, 401, 403, 404 | Bug Reports/History report exports; `tools.report_generator.*` |
| POST | `/runs/{run_id}/report/save` | conditional | none | `MessageResponse {message}` | 401, 403, 404 | History “Save a copy to reports/”; `tools.report_generator.report_html`, `config.settings.REPORTS_DIR` |
| DELETE | `/runs/{run_id}` | conditional | none | `MessageResponse` | 401, 403, 404 | History delete; ownership-filtered `Database.delete_run` (NEW ownership guard) |
| POST | `/batch` | conditional | multipart `files[]` (`.py` and optional `*_requirements.txt`), `requirements` text, `options` JSON | `BatchAccepted {batch_id, status, files: str[], warnings: str[]}` | 400, 401, 413, 422 | Studio batch upload/run; `core.batch.pair_files/run_batch` in background (NEW batch job wrapper) |
| GET | `/batch/{batch_id}` | conditional | none | `BatchStatus {batch_id, status, runs: RunSummary[], summary: object[]}` | 401, 403, 404 | Studio batch result; `core.batch.summary_rows` |
| GET | `/batch/{batch_id}/download` | conditional | none | `FilePayload` | 401, 403, 404 | Batch ZIP; `core.batch.batch_zip` |
| GET | `/learning` | conditional | none | `LearningList {items: LearnedFix[]}` | 401 | Learning page; `Database.list_fix_memory(user_id)` |
| PATCH | `/learning/{fix_id}` | conditional | `LearningRatingRequest {rating: -1\|0\|1}` | `MessageResponse` | 400, 401, 403, 404, 422 | Approve/reject; `Database.rate_fix_memory` |
| PATCH | `/learning/run/{run_id}` | conditional | `LearningRatingRequest {rating: -1\|0\|1}` | `MessageResponse` | 400, 401, 403, 404, 422 | Fix Review “Good fix/Bad fix”; `Database.rate_fix_memory(run_id=..., user_id=...)` |
| DELETE | `/learning/{fix_id}` | conditional | none | `MessageResponse` | 401, 403, 404 | Delete example; `Database.delete_fix_memory` |
| DELETE | `/learning` | conditional | none | `CountResponse` | 401 | Erase all learned fixes; `Database.delete_fix_memory(user_id)` |
| GET | `/learning/export` | conditional | `format: chat\|prompt`, `approved_only?` | `FilePayload` | 400, 401 | Training export; `tools.training_export.to_chat_jsonl/to_prompt_jsonl` |
| GET | `/settings` | conditional | none | `SettingsResponse` (secret-free) | 401 | Settings display; `Settings.load`, `llm_status` |
| PATCH | `/settings` | admin | `SettingsUpdateRequest` (non-secret settings only) | `SettingsResponse` | 401, 403, 422 | Settings controls; `Settings.save` |
| GET | `/settings/models` | conditional | none | `ModelList {providers, models, hints}` | 401 | Provider/model selector; `config.settings.PROVIDERS/MODEL_HINTS` |
| GET | `/settings/availability` | conditional | none | `Availability {docker: SandboxStatus, ollama: OllamaStatus}` | 401 | Engine/sandbox status; `tools.test_runner.sandbox_status`, `tools.llm_client.detect_ollama` |
| POST | `/settings/test-connection` | admin | `ConnectionTestRequest {provider?, model?}` | `ConnectionTestResponse {ok, message}` | 401, 403, 422 | Settings test connection; `LLMClient.test_connection` |
| POST | `/settings/retention/purge` | admin (or anonymous if login optional) | `PurgeRequest {days: int}` | `CountResponse {deleted: int}` | 400, 401, 403, 422 | Settings “Purge old runs now”; `Database.purge_older_than(days, user_id)` |
| POST | `/settings/retention/scrub-sources` | admin (or anonymous if login optional) | none | `CountResponse {scrubbed: int}` | 401, 403 | Settings “Erase all stored source code”; `Database.scrub_retained_sources(user_id)` |
| GET | `/samples` | conditional | none | `SampleList {items: SampleFile[]}` | 401 | Studio sample choices; NEW listing of files already present under `sample/` |
| GET | `/samples/{name}` | conditional | none | `SampleContent {filename, code, requirements}` | 400, 401, 404 | Load sample with planted bugs / class sample; existing `sample/` files (NEW safe allowlisted reader) |
| POST | `/analysis/preview` | conditional | `AnalysisPreviewRequest {code, filename}` | `AnalysisResponse` | 401, 413, 422 | Studio quick analysis; `tools.code_analyzer.analyze_source` |

## Coverage: existing page actions to endpoints

| Page action group from `docs/INVENTORY.md` | Endpoint coverage |
|---|---|
| Account: sign in/up/out, profile, password, own runs/account, admin users/roles/access | `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/password`, `/auth/account/runs`, `/auth/account/delete`, `/auth/admin/users` (GET/POST/PATCH/DELETE), `/auth/admin/settings` (GET/PATCH) |
| Dashboard metrics, timeline, recent runs/events/findings | `/dashboard`, `/runs` |
| Studio: upload/paste/preview, single run, sample loading, options | `POST /analysis/preview`, `POST /runs`, `GET /samples`, `GET /samples/{name}`, `GET /settings/availability` |
| Studio batch upload, results, ZIP | `POST /batch`, `GET /batch/{batch_id}`, `GET /batch/{batch_id}/download` |
| Agent Monitor: run picker, pipeline, events, plan and status | `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/status`, `GET /runs/{run_id}/events` |
| Code Quality: analysis, static findings, functions | `GET /runs/{run_id}/analysis` |
| Bug Reports: filters, findings, report CSV/JSON/HTML | `GET /runs/{run_id}/findings`, `GET /runs/{run_id}/report`, `GET /runs/{run_id}/report/download` |
| Test Generator: list/edit/add/export/run selected/all | `GET /runs/{run_id}/tests`, `/tests/download?format=pytest`, `PATCH /runs/{run_id}/tests`, `POST /runs/{run_id}/tests`, `POST /runs/{run_id}/execution`, `GET /runs/{run_id}/report/download?format=tests_csv` |
| Test Results: cases and outcomes | `GET /runs/{run_id}/tests` |
| Auto-Fix: view/rate, download source/diff, retry hint | `GET /runs/{run_id}/fix`, `/fix/diff`, `/fix/download`, `POST /fix/retry`, `PATCH /learning/run/{run_id}` |
| History: search/filter/open/delete/report/compare | `GET /runs`, `GET /runs/{run_id}`, `DELETE /runs/{run_id}`, report endpoints, findings endpoints |
| Learning: list/rate/delete/export/toggles | `GET/PATCH/DELETE /learning`, `/learning/{fix_id}`, `GET /learning/export`, `GET/PATCH /settings` |
| Settings: providers, connection, sandbox, retention, persistence | `/settings`, `/settings/models`, `/settings/availability`, `/settings/test-connection`, `/settings/retention/purge`, `/settings/retention/scrub-sources` |
| History: “Save a copy to reports/” | `POST /runs/{run_id}/report/save` |
| Shared navigation/run picker, empty-state links, refresh | `GET /runs`, `/runs/{run_id}/status`, `/runs/{run_id}/events` |

Run comparison is computed by the frontend from two run details and findings. “Make active run” is page selection state and uses an already fetched run; no server-side session selection is needed.

## Schema glossary

The names used in the endpoint table are concrete Pydantic models (or lists of those models); no request/response is an untyped dictionary. Collection wrappers have an `items` list and all IDs are strings except SQLite user/fix IDs (integers).

- `User {id:int, username:str, role:"user"|"admin"}`. Never contains a password or hash. `AuthResponse {user:User, authenticated:bool}`; `UserList {items:User[]}`; `AuthUserResponse {user:User}`.
- `RegisterRequest {username:str, password:str}`; `LoginRequest` has the same shape; `PasswordChangeRequest {current_password:str, new_password:str}`; `AdminCreateUserRequest {username:str, password:str, role:"user"|"admin"}`; `RoleUpdateRequest {role:"user"|"admin"}`; `AccessSettings {require_login:bool, allow_signup:bool}`; `ConfirmRequest {confirm:bool}`.
- `MessageResponse {message:str}`; `CountResponse {deleted?:int, scrubbed?:int}`; `Health {status:str}`; `FilePayload {filename:str, media_type:str, content_base64:str}`.
- `RunCreateRequest {code:str, filename:str="submitted.py", objective:str, requirements:str, targets:str[], project:str, categories:str[], max_tests_per_function:int, pause_for_review:bool, auto_fix:bool, fix_mode:"safe"|"ai", improve_code:bool=true}`. Limits and category values follow `RunRequest`/`Settings.clamp`; source length is capped at 200,000 characters as in Studio. `RunAccepted {run_id:str, status:str}`.
- `RunSummary {id:str, created_at:str, finished_at:str|null, project:str, filename:str|null, status:str, verdict:str, tests:int, passed:int, failed:int, errors:int, findings:int, iterations:int}`; `RunList {items:RunSummary[]}`. `RunStatus {run_id:str, status:str, iteration:int, max_iterations:int, stop_reason:str, finished_at:str|null, fix:object|null, improvement:object|null}`. `RunDetail {run:RunSummary, request:RunRequestView, analysis:AnalysisResponse|null, tests:TestCase[], results:TestResult[], findings:Finding[], report:ReportResponse|null, fix:FixResponse|null, events:AgentEvent[]}`; `RunRequestView` excludes source unless retention/run state makes it available and never includes secrets.
- `AgentEvent {id:int, run_id:str, agent:str, iteration:int, status:str, task:str, input_summary:str, output_summary:str, reason:str, tool_calls:ToolCall[], warnings:str[], errors:str[], started_at:str|null, ended_at:str|null, duration_ms:int}`; `AgentEvents {items:AgentEvent[]}`. `ToolCall {name:str, summary:str, result:str, ok:bool, duration_ms:int}`.
- `AnalysisResponse {ok:bool, syntax_error:object|null, meta:object, metrics:object, functions:object[], classes:object[], findings:object[], dependencies:str[], third_party:str[], ruff:object}`. Nested keys follow `tools.code_analyzer.analyze_source` and persisted `analysis_json`. `AnalysisPreviewRequest {code:str, filename:str}`.
- `Finding {finding_id:str, category:str, title:str, description:str, severity:str, file:str|null, line:int|null, function:str|null, rule:str, evidence:str, expected:str, actual:str, suggested_fix:str, validation_status:str, source_tool:str, case_ids:str[]}`; `FindingList {items:Finding[]}`.
- `TestCase {id:str, name:str, category:str, function:str, args:str, kwargs:str, ctor:str|null, setup:str|null, expect:object, rationale:str, source:str, oracle:str, enabled:bool, iteration:int, result:TestResult|null}`; `TestCaseList {items:TestCase[]}`. `TestResult {case_id:str, status:str, actual:str, detail:str, exception_type:str|null, exception_message:str|null, traceback:str, stdout:str, line:int|null, duration_ms:float, isolation:str}`. `TestCaseCreateRequest` has function/name/args/kwargs/expectation text/optional ctor/setup; `TestCaseEditsRequest {items:TestCaseEdit[]}`; `TestCaseEdit {id:str, enabled:bool, name:str, args:str, kwargs:str, ctor:str|null, expected:str}`. `ExecutionRequest {selected_ids:str[]|null, run_all:bool}`.
- `FixResponse {status:str, mode:str, diff:str, applied:object[], skipped:object[], before:object, after:object, regressions:object[], fixed_cases:str[], unresolved:object[], hint:str, improvement:Improvement|null}`. `Improvement` contains `status`, `original_source`, `fixed_source`, `improved_source`, both pipeline diffs, before/after metrics, applied rule descriptions, verification status, and not-fixed items. `FixRetryRequest {hint:str}`.
- `ReportResponse` is the persisted report JSON shape (run metadata, findings, cases/results and summary), validated by its dedicated Pydantic nested models; binary/download exports are always `FilePayload`.
- `DashboardResponse {metrics:object, trend:object[], latest_runs:RunSummary[], latest_events:AgentEvent[], latest_findings:Finding[], timeline:AgentEvent[]}`.
- `BatchAccepted {batch_id:str, status:str, files:str[], warnings:str[]}`; `BatchStatus {batch_id:str, status:str, runs:RunSummary[], summary:object[]}`. `options` in multipart batch requests is JSON equivalent to `BatchOptions` (project, objective, categories, test cap, fix mode); max 25 `.py` files, paired optional `*_requirements.txt`.
- `LearnedFix {id:int, created_at:str, run_id:str|null, function_name:str, problem:str, before_code:str, after_code:str, provider:str, model:str, rating:int, times_used:int}`; `LearningList {items:LearnedFix[]}`; `LearningRatingRequest {rating:-1|0|1}`.
- `SettingsResponse` contains public `Settings` values, provider readiness and never any key/secret. `SettingsUpdateRequest` allows only declared non-secret `Settings` fields. `ModelList {providers:object[], models:object[], hints:dict[str,str]}`; `Availability {docker:SandboxStatus, ollama:OllamaStatus}`; `SandboxStatus {available:bool, secure:bool, label:str, reason:str}`; `OllamaStatus {available:bool, models:str[]}`; `ConnectionTestRequest {provider:str|null, model:str|null}`; `ConnectionTestResponse {ok:bool, message:str}`; `PurgeRequest {days:int}`.
- `SampleFile {name:str, code_name:str, requirements_name:str|null}`; `SampleList {items:SampleFile[]}`; `SampleContent {filename:str, code:str, requirements:str}`.

## New adapter functionality

Routes marked NEW or described as wrappers do not claim pre-existing HTTP behavior. Their only new behavior is transport/session/job orchestration around the real functions named above. The following are newly introduced backend concerns: `/api` routing, token persistence and revocation, async job IDs/status, safe sample listing, and JSON error envelopes. No agent or orchestrator algorithm is replaced.
