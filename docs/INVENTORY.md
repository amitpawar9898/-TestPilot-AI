# Phase 0: repository inventory

Read-only reconnaissance for the React + HTTP API migration. The repository currently has no API or `docs/` directory (before this file was added). Source names and behavior below were checked in the working tree.

## Baseline test run

Command used because `pytest` is not on PATH: `\.venv\Scripts\python.exe -m pytest -q -rA` (pytest 9.1.1). Output summary:

```text
ERROR views/test_generator.py::tests_csv
66 passed, 1 error in 119.18s (0:01:59)
```

The collection error is pre-existing: `views/test_generator.py` imports `tests_csv` from `tools.report_generator`, and pytest collects that imported callable as a test, then reports `fixture 'report' not found`. The 66 listed tests passed. `pytest -q` directly was also attempted but could not start because PowerShell reported `pytest : The term 'pytest' is not recognized`; `.venv`'s Python invocation ran the suite. This is a baseline, not a green gate.

## View to functionality map

| View / page | Existing calls and data | Display and user actions |
|---|---|---|
| `views/account.py` — Account / login gate | `core.auth.authenticate`, `register`, `change_password`; `Database.count_users`, `list_users`, `purge_older_than`, `delete_user`, `count_admins`, `set_role`; `Settings.save`, `login_required` | Sign-in and registration forms; profile, sign out, password change; delete own runs/account; admin user list, require-login and signup toggles, add user, change role, delete user. Destructive actions have an in-page confirmation. |
| `views/agent_monitor.py` — Agent Workflow Monitor | `Database.get_events`, `get_run`; `RunState.agent_dicts`; `components.workflow_view.agents_from_events`, `render_pipeline`, `render_details`; shared `run_picker`, `runs_store` | Run picker, status/cycle/isolation metrics, refresh, current pipeline with task and tool detail, Planner plan and follow-up cycles, stored event log. |
| `views/bug_reports.py` — Bug Reports | `Database.get_findings`, `get_report`; `tools.report_generator.findings_csv`, `report_json`, `report_html`; shared run picker/badges | Filter severity, validation status, and category; export filtered findings CSV and report JSON/HTML; inspect grouped findings, evidence, expected/actual, location, and suggested fix. |
| `views/code_quality.py` — Code Quality Analysis | `Database.get_analysis`; shared run picker; Plotly and metric/style helpers | Static syntax error or LOC/SLOC, function/class, complexity and finding metrics; complexity and severity charts; findings, function signatures/complexity/raises/auto-testability, Ruff status, dependencies. Read-only. |
| `views/code_studio.py` — Fix my code / Code Analysis Studio | `core.orchestrator.Orchestrator.start`; `core.state.RunRequest`; `core.batch.pair_files`, `run_batch`, `summary_rows`, `batch_zip`; `tools.code_analyzer.analyze_source`; `tools.test_runner.sandbox_status`; `Settings`; shared stores, navigation, status and verdict helpers | Paste code or upload one `.py`; load function/class samples; requirements, project, target functions, objective, categories, test cap and pause-for-review settings; Fix my code or analyze-only action; analysis preview; batch upload with per-file requirements, process batch, inspect a file, download ZIP and open Auto-Fix; view result summary, fixed code/diff download, tests/findings, monitor/reports links. This is the actual “Fix my code” home page and Studio batch tab; both are in `views/code_studio.py`. |
| `views/dashboard.py` — Dashboard | `Database.metrics`, `runs_trend`, `list_runs`, `timeline`, `recent_events`, `get_findings`; metric/style/badge helpers | Run/test/pass metrics, outcome and trend charts, latest agent timeline, severity chart, recent runs/activity/findings. Empty-state link to Studio. |
| `views/fix_review.py` — Auto-Fix | `Database.get_report`, `rate_fix_memory`; `core.orchestrator.Orchestrator.retry_fix`; shared run picker/store/settings; workflow display | Patch status and before/after metrics, diff and skipped fixes; download fixed source and diff; load patched code into Studio; rate learned fix good/bad; enter hint and retry unresolved issues. |
| `views/history.py` — Test History | `Database.list_runs`, `get_report`, `get_findings`, `delete_run`, `purge`-related ownership via DB; report generator `report_json`, `report_html`, `findings_csv`, `tests_csv`; report directory; run selection | Search, status/period/verdict filters; open a run; confirm permanent delete; download report and CSVs, save HTML copy and preview; compare two runs' metrics and new/resolved/persisting findings. |
| `views/settings.py` — Settings | `Settings.load/save`; `LLMClient.from_settings/test_connection`; `sandbox_status`; `Database.purge_older_than`, `scrub_retained_sources`; `DATA_DIR`, execution mode and unsafe-local setting | Fix engine/provider/model controls and connection test; execution mode, image, timeout/memory/iteration/test limits; report/learning/source-retention controls; purge old runs and erase stored source; save settings; security information. |
| `views/test_generator.py` — Test Generator | `Database.get_cases`, `get_results`, `get_report`; `core.orchestrator.Orchestrator.continue_run`; `tools.test_generator.export_pytest`, `parse_expect`, `validate_case`, `format_expect`; `tools.report_generator.tests_csv`; shared run selection | Filter and edit case run flag/name/constructor/args/kwargs/expectation; apply edits; download pytest and result CSV; run selected/all tests; add validated manual case. Supports a review-paused run and fresh reruns. |
| `views/test_results.py` — Test Execution Results | `Database.get_run`, `get_cases`, `get_results`; `tools.test_generator.format_expect`; shared run selection/status and metrics | Outcome and duration charts; filters for status/category/function; inspect individual case input, rationale/oracle, expected/actual, detail, traceback and captured stdout. |
| `views/training.py` — Learning and Training | `Database.list_fix_memory`, `rate_fix_memory`, `delete_fix_memory`, `delete` all memory; `Settings.save`; `tools.training_export.to_chat_jsonl`, `to_prompt_jsonl` | Opt-in learning and memory use toggles; inspect before/after examples, approve/reject/delete; export approved/all eligible JSONL formats; erase all learned fixes. |

Shared `components/common.py` owns cached DB creation, settings load, current user/user ID, logout, per-browser `runs` store, active-run selection, navigation and common display helpers. `components/workflow_view.py`, `metric_cards.py`, and `styles.py` render workflow details, metric cards and chart styling.

## Streamlit coupling

Search performed over `agents/ core/ tools/ database/ config/ views/ components/ app.py tests/` with `rg -n -i "streamlit|st\.session_state|st\.cache" ...`.

| Location outside `views/` and `components/` | Coupling found |
|---|---|
| `app.py:1-2,23-28,46,75` | Streamlit entry point, session state, navigation/session cleanup. |
| `config/settings.py:2,140,149,153-154` | Secrets docstring and `get_secret` fallback imports `streamlit` and reads `st.secrets`. |
| `database/db.py:1` | Streamlit appears only in a descriptive docstring (“safe with Streamlit threads”); implementation uses short-lived SQLite connections. |
| `agents/`, `core/`, `tools/`, `tests/` | No Streamlit imports/session/cache references found. |

Views/components have broad direct UI coupling: every view imports `streamlit`; `components/common.py` uses `st.cache_resource`, `st.cache_data`, `st.session_state` and `st.switch_page`; `components/styles.py`, `metric_cards.py`, and `workflow_view.py` render through Streamlit. `views/code_studio.py` also uses `st.cache_data` for analysis.

## Agents and workflow (from code)

There are **seven** registered agents, in `core/orchestrator.py:AGENTS` and `core/state.py:AGENT_ORDER`:

| Key / display name | Responsibility in code |
|---|---|
| `planner` / Planner | Creates initial execution plan; after Critic asks for more evidence, plans a follow-up cycle focused on named functions. |
| `code_analysis` / Code Analysis | Parses Python and collects structure, static findings and analysis metadata; syntax failure blocks testing. |
| `test_generation` / Test Generation | Creates cases and pytest representation from analysis, requirements/doctests and configured categories; follow-up adds cases for requested focus. |
| `test_execution` / Test Execution | Runs enabled cases through configured runner/isolation and records actual outcomes. |
| `critic` / Critic & Validation | Assesses outcomes, evidence and test coverage; may request another bounded test cycle and records findings/verdict. |
| `fix` / Fix | Applies deterministic and optionally model-generated edits; reruns the same cases and checks regressions. |
| `reporting` / Reporting | Consolidates findings/results and persists report. |

`core/workflow.py:next_stage` defines Planner → analysis → generation → execution → Critic. Planner failure proceeds to reporting; analysis or first-round generation failure marks blocked and goes to reporting. Generation can pause for user review. Critic may return to Planner while `iteration < max_iterations`; the orchestrator increments iteration and archives prior Planner/generation/execution/Critic records. Follow-up generation skips to execution only if new cases exist, otherwise reporting. After Critic, `auto_fix` selects Fix or marks Fix blocked and reports. Fix goes to reporting. `Settings.clamp` caps max iterations at 5; default is 2. `continue_run` resumes a review or selected tests; `retry_fix` runs Fix and Reporting directly.

## State, persistence, authentication, and run duration

- `RunRequest` and `RunState` are dataclasses in `core/state.py`. The orchestrator constructs a state, runs each agent synchronously inside `_drive`'s loop, catches agent errors, emits optional update callbacks, and persists events/run state after each step. `start()` returns only after the pipeline ends or pauses; calling it inside an HTTP request unchanged would block that request. The UI currently renders progress through callbacks while the same call is active.
- `database/schema.sql` stores users, projects, runs, sources/analysis, agent events, test cases/results, findings, reports, and optional fix memory. `Database` in `database/db.py` wraps SQLite with a short-lived connection per method. Run ownership is `runs.user_id` (NULL for anonymous); read/list queries accept user ownership filters.
- Auth logic in `core/auth.py`: salted scrypt hashes (PBKDF2 fallback), username/password validation, first account becomes admin, generic login failure for unknown/wrong credentials, five failures trigger a 15-minute lockout, and `SESSION_HOURS = 8`. `authenticate` records successful/failed attempts; `session_expired` checks elapsed time. `change_password` revalidates new credentials. `views/account.py` currently stores public user plus `login_at` in Streamlit session state. Admin UI manages users, roles, signup and required-login settings. `config/settings.py:login_required` also supports `TESTPILOT_REQUIRE_LOGIN=1`. There is no server-issued token currently.
- Source retention defaults off; when on, source is stored in `sources.content`. The UI session also holds current run states and submitted source. Logout clears session state.
- Run duration is synchronous and depends on configured `total_timeout`, per-test timeout, Docker or explicitly enabled trusted local mode. If neither execution mode is available, runner behavior is `not_run` (not a pass); this is visible in README and runner code. Long runs are therefore an API design risk and need a background task/polling contract.

## Risk notes for migration

- **Long-running work:** synchronous orchestration and batch execution can exceed ordinary request timeouts; API must start work separately and persist status/events.
- **Session state:** active run objects, current source, upload state and user are currently browser-session scoped. Durable run records do not necessarily retain source or patched source.
- **Uploads/downloads:** individual `.py` upload, multi-file batch pairing with optional `*_requirements.txt`, 200,000-character single-file UI limit, batch ZIP, fixed code/diff, pytest/CSV/JSON/HTML/JSONL exports; API needs explicit size/type/ownership behavior.
- **Auth and ownership:** current identity is a Streamlit session, but DB data isolation is by `user_id`; all API read/write/download paths must enforce that ownership. Admin settings and user-management actions need role checks. Cookies/tokens must preserve the 8-hour expiry and logout semantics.
- **Execution safety:** submitted source runs only through `tools/test_runner.py` configured execution modes; API must not execute it in its own process or loosen the Docker restrictions.
- **Configuration secret lookup:** `config/settings.py` imports Streamlit only for `st.secrets`; this is the sole non-UI runtime coupling besides the DB docstring.

## Inventory evidence greps

Representative commands run against this repository:

```powershell
rg -n "class .*Agent|AGENTS =|AGENT_ORDER" agents core\orchestrator.py core\state.py
rg -n "def next_stage|request_more_tests|max_iterations|_fix_or_report" core\workflow.py
rg -n "Orchestrator\(settings, db|run_batch\(|pair_files\(|Fix my code|studio_batch" views\code_studio.py
```

These confirm sample names and the workflow/batch/function references recorded above. The relevant definitions were opened in `core/orchestrator.py`, `core/workflow.py`, `core/state.py`, `core/auth.py`, `database/db.py`, `database/schema.sql`, `tools/llm_client.py`, and the view sources.
