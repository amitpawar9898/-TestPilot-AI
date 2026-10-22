# CA-3 Final Checklist

## Mandatory deliverables

| # | Deliverable | Evidence in this repository |
|---|---|---|
| 1 | Clear problem statement and domain justification | [CA3_PROBLEM_STATEMENT.md](CA3_PROBLEM_STATEMENT.md) describes the Python QA problem, domain fit, oracle model, intended benefit, and limits. |
| 2 | Existing challenge and proposed solution | [CA3_PROBLEM_STATEMENT.md](CA3_PROBLEM_STATEMENT.md) and README explain the Streamlit-era repository foundation and the React/FastAPI proposal. |
| 3 | Agent roles, responsibilities, and orchestration workflow | [CA3_AGENTS_AND_ORCHESTRATION.md](CA3_AGENTS_AND_ORCHESTRATION.md) maps all seven registered agents, tools, transitions, decisions, bounded cycles, and fix verification. |
| 4 | Complete architecture diagram with data flow, tools, and decisions | [ARCHITECTURE.md](ARCHITECTURE.md) includes Mermaid component and decision flows; [architecture.svg](architecture.svg) is the rendered vector diagram. |
| 5 | Working UI PoC using dummy/synthetic data | `frontend/` contains the React application; `sample/` contains synthetic planted-bug modules. [DEMO_SCRIPT.md](DEMO_SCRIPT.md) gives the walkthrough and [screenshots](screenshots/) captures the browser flow. |
| 6 | Output validation, results, limitations, and future scope | [CA3_VALIDATION_RESULTS.md](CA3_VALIDATION_RESULTS.md) contains measured per-sample counts, timing, findings, regressions, limitations, and future scope; [ca3_validation_results.json](ca3_validation_results.json) is the source data. |
| 7 | Presentation: problem, solution, architecture, demonstration | [TestPilot_CA3.pptx](../presentation/TestPilot_CA3.pptx) has eight slides with speaker notes and a real application screenshot. [DEMO_SCRIPT.md](DEMO_SCRIPT.md) is the five-minute presentation guide. |

## Evaluation criteria

| Criterion | Where an evaluator can verify it |
|---|---|
| Problem identification | [Problem statement](CA3_PROBLEM_STATEMENT.md), slide 2, and the sample requirements in `sample/`. |
| Domain understanding | Domain justification, confirmed-vs-heuristic oracle explanation, and limitations in [problem statement](CA3_PROBLEM_STATEMENT.md) and [validation results](CA3_VALIDATION_RESULTS.md). |
| Agent architecture | Seven-agent responsibilities and the rendered [architecture diagram](architecture.svg). |
| Orchestration | Conditional workflow diagram and exact routing rules in [agent orchestration](CA3_AGENTS_AND_ORCHESTRATION.md) and [architecture decisions](ARCHITECTURE.md). |
| Working PoC | React application in `frontend/`, FastAPI service in `api/`, one-command [run_dev.ps1](../run_dev.ps1), browser screenshots, and Playwright E2E workflow. |
| Output validation | [Validation results](CA3_VALIDATION_RESULTS.md), machine-readable run evidence, live [endpoint verification](ENDPOINT_VERIFICATION.md), regression-checked fixes, and test commands below. |
| Technical understanding | [API contract](API_CONTRACT.md), [decisions](DECISIONS.md), architecture/agent documentation, and evaluator Q&A in [DEMO_SCRIPT.md](DEMO_SCRIPT.md). |

## Final verification record

- Backend: `python -m pytest -q` — 71 passed, 1 deprecation warning.
- Frontend: `npm run build` — TypeScript and Vite build passed; `npm run lint` — zero errors (62 warnings); `npm run routecheck` — 54/54 operations used, zero mismatches.
- Live API: `python scripts/verify_endpoints.py` — 54/54 response shapes validated against OpenAPI.
- Browser: `npm run e2e` — one Chromium end-to-end flow passed.
- Fresh clone: Python dependency installation, `npm ci`, backend tests (71 passed), frontend build and route check, `run_dev.ps1` service health checks, and the complete Playwright synthetic-sample flow passed in a temporary clone without copied environments, node modules, database, or `.env`.
- Safety: the bundled sample validation and browser sample run explicitly selected local trusted mode only for synthetic repository code; local trusted mode is not a sandbox. The app's default execution mode remains Docker.

Warnings/limits: the backend suite emits the existing Starlette/HTTPX TestClient deprecation warning; the endpoint verifier emits the jsonschema `RefResolver` deprecation warning. The sample evaluation is three planted-bug files and not a general accuracy benchmark. AI fixes were not evaluated.
