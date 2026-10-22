# TestPilot AI

Agentic Python software-testing and bug-detection platform. A bounded orchestrator coordinates seven agents (Planner, Code Analysis, Test Generation, Test Execution, Critic, Fix, Reporting). The application uses a **React + TypeScript** browser client, a **FastAPI** JSON API, SQLite persistence, and a sandbox-aware test runner.

## Start on Windows

Requirements: Python 3.11 or 3.12, Node.js 20.19+ or 22.12+ ([Vite requirement](https://vite.dev/guide/)), npm, and (for recommended code execution) Docker Desktop.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Set-Location frontend
npm ci
Set-Location ..
```

Start both services with one command:

```powershell
.\run_dev.ps1
```

Open <http://127.0.0.1:5173>. The API is at <http://127.0.0.1:8000>; interactive API documentation is at <http://127.0.0.1:8000/docs>. The launcher waits for both services and writes logs and process IDs under `reports/`. Stop them with `.\stop_dev.ps1`.

## Run with Docker Compose

Install and start Docker Desktop and Ollama, then from the project root run:

```powershell
# One command builds and starts Docker, then checks API, web, and bundled samples.
.\verify_docker.ps1
```

The script confirms API status `ok`, frontend status `200`, and bundled sample availability. Open <http://localhost:5173>; the API docs are at <http://localhost:8000/docs>. Compose keeps SQLite data and reports in named volumes and routes the local Ollama model through `host.docker.internal`. View logs with `docker compose logs -f api frontend`; stop with `docker compose down`.

The API container does not mount the host Docker socket. This keeps the web service from gaining control of Docker, but means its isolated submitted-code test runner cannot launch sibling containers in this Compose setup. For actual sandboxed test execution, run the backend directly on the host with Docker Desktop (`.\run_dev.ps1`) and keep the frontend in Docker, or use an isolated remote runner. Do not enable local trusted execution for arbitrary uploaded code.

PowerShell may block virtual-environment activation; if needed run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. macOS/Linux can create a Python virtual environment and run Uvicorn and Vite separately from the project root and `frontend/` directories.

## Supported code and test evidence

TestPilot AI analyzes Python source. It generates callable-function tests where possible; for a top-level script with no functions, it runs one isolated smoke check for an unhandled exception. A smoke check does not verify printed output or prove the logic is correct. Put script logic in callable functions and add requirement examples with expected results to check behavior. Safe syntax repair is limited to a missing block colon when the corrected file parses; arbitrary bugs cannot be automatically solved in every uploaded program.

## Run an analysis

1. Open **Fix my code** and paste or upload a `.py` module, or select the bundled sample.
2. Add requirements such as `total(2, 3) == 5` or `parse_value("bad") raises ValueError`. These provide confirmed expected-value tests. Docstring doctests also provide confirmed oracles.
3. Run the analysis. Review the generated tests, findings, agent activity, and **Auto-Fix** diff.
4. Download a verified fixed file or report if useful. The original source is not overwritten.

Generated robustness cases without explicit expected values use heuristic checks such as no-crash, return type, determinism, and invalid-input handling. Reports distinguish confirmed expectations from heuristic evidence. A passing test suite only describes the cases that actually ran; it is not a proof that the program is bug-free.

## Test execution safety

- **Docker mode (recommended)** runs submitted tests in a disposable network-disabled container with resource limits, a read-only source mount and filesystem, dropped capabilities, and a non-root user. Docker Desktop must be installed and running; the runner pulls its configured Python image when needed.
- **Local trusted mode** runs a local process and is **not a sandbox**. Enable only for code you trust by setting `TESTPILOT_ALLOW_UNSAFE_LOCAL=1` in the server environment and selecting local trusted mode in Settings.
- If Docker is unavailable and local trusted mode is not explicitly enabled, tests remain blocked/not-run. Unexecuted tests are never marked passed. Static analysis parses source and does not execute it.

The in-process test harness should not be relied on against malicious code. For arbitrary submissions, keep Docker execution enabled.

## Auto-fix behavior

The built-in engine applies known deterministic changes (for example mutable defaults, bare `except`, `== None`, observed division-by-zero guards, percentage errors, and 1-based pagination where supported). Candidate changes are syntax-checked and re-tested with the same cases; a previously passing case that fails causes rejection. Smart changes must reduce failures before acceptance. Remaining logic issues need an explicit requirement, a human change, or an optional AI provider. AI rewrites are also re-tested and can be rejected. Fixes are labeled verified only when tests provide evidence; otherwise they are unverified.

After a successful, non-regressing fix, **Also improve code quality** (on by default) runs safe Ruff cleanup and formatting, then reruns the same tests. The review shows Original, Fixed, and Improved versions, metrics, the change list, unresolved issues, and downloads. If the runner does not execute the tests, an edit is labeled unverified. No safe edits are reported without claiming improvement. Local Ollama may offer a bounded readability refactor when detected; cloud providers are not used for this improvement stage.

Ollama can be used locally when available. To enable a cloud LLM, copy `.env.example` to `.env`, add your provider API key, choose `TESTPILOT_PROVIDER=openai` (or `anthropic` / `openai_compatible`), and set `TESTPILOT_LLM_CONSENT=1` only after agreeing that submitted source code may be sent to that provider. `TESTPILOT_MODEL` can override the default model. Restart the backend after changing `.env`. These environment values override the Settings page; omit them to configure a provider in Settings instead. API keys stay on the backend and are never returned to the browser. A ChatGPT subscription does not include API access; use a provider API key with billing enabled. AI is optional; the built-in deterministic engine requires none.

## Accounts and data

Open **Account** in the app to create the first administrator account. Login is optional by default; administrators can require it in Account settings or the server can set `TESTPILOT_REQUIRE_LOGIN=1`. User-owned runs, reports, findings, and learned fixes are separated. Passwords use salted scrypt hashes, sessions expire after eight hours, and failed login attempts are rate-limited. Reset an account password on the host with `python -m core.auth reset-password <username>`.

The local service uses HTTP on loopback. Put it behind HTTPS before exposing it to other users. The default SQLite database and reports are stored in `data/` and `reports/`. Set `TESTPILOT_DATA_DIR` or `TESTPILOT_REPORTS_DIR` to change those server paths. `.env.example` lists supported environment configuration.

## Additional features

- Batch analysis accepts up to 25 independent Python files and optional per-file requirements.
- Fix retry can pass a hint to an enabled AI provider; verified fixes can optionally be stored as local learning examples.
- Approved fix examples can be exported as chat or prompt/completion JSONL. Export data includes code and should be kept private. The application does not fine-tune model weights itself.
- Reports include findings and execution evidence and can be downloaded in supported formats.

## Development and verification

```powershell
# Backend unit/API tests
python -m pytest -q

# Frontend checks
Set-Location frontend
npm run build
npm run lint
npm run routecheck
npm run e2e   # Playwright Chromium must be installed once: npx playwright install chromium
```

With the backend running, `python scripts/verify_endpoints.py` checks every OpenAPI operation and response shape against a temporary verification setup. Browser screenshots and the endpoint matrix are in `docs/`.

## CA-3 deliverables

- [Problem statement and domain choice](docs/CA3_PROBLEM_STATEMENT.md)
- [Seven agents and orchestration](docs/CA3_AGENTS_AND_ORCHESTRATION.md)
- [Architecture diagram](docs/ARCHITECTURE.md) · [SVG](docs/architecture.svg)
- [Measured results on all bundled samples](docs/CA3_VALIDATION_RESULTS.md)
- [Five-minute demo and evaluator Q&A](docs/DEMO_SCRIPT.md)
- [Phase progress and verification evidence](docs/PROGRESS.md)
- [Final CA-3 checklist](docs/FINAL_CHECKLIST.md)

The measured sample runs are demonstrations over three intentionally buggy repository samples, not a general accuracy benchmark. They used local trusted mode on repository code; Docker is the recommended production execution mode.

## Project layout

```
api/                FastAPI routes, schemas, auth/session and worker adapters
agents/             Seven agents and tool-call context
core/               Orchestrator, workflow transitions, shared state, batch logic
database/           SQLite schema and data-access layer
tools/              Analyzer, test generator/runner, fixer, LLM and report tools
frontend/           React + TypeScript application, OpenAPI client and Playwright E2E
scripts/            Endpoint, route, sample-validation and presentation utilities
sample/             Bundled buggy Python modules and requirements
tests/              Backend and API tests
docs/               API contract, verification records and CA-3 materials
```
