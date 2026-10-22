# Implementation decisions

## Phase 1

- **Run progress:** use `POST /api/runs` to create and start a background-thread job, returning a run ID immediately; clients poll `GET /api/runs/{run_id}/status` and `GET /api/runs/{run_id}/events`. This is simpler to operate on Windows than an SSE connection and directly addresses the synchronous `Orchestrator.start()` found in reconnaissance. Run records and agent events already persist in SQLite; in-process workers will preserve existing agent/orchestrator behavior.
- **Authentication transport:** use an opaque random bearer token in an `HttpOnly`, `SameSite=Lax` cookie (also accept the `Authorization: Bearer` form for scripts). Store only a token hash and expiration in a new SQLite session table; logout revokes it. This supports revocation and multi-worker lookup, unlike a process-local session map, and leaves `core/auth.py` password, generic-error, lockout, and validation behavior in place. Session lifetime remains eight hours.
- **API shape:** `/api` prefix; JSON object responses carry explicit Pydantic response models. Download routes return a typed `FilePayload` JSON object containing filename, media type, and base64 data so the API contract and generated client remain typed.
- **Dependencies:** installed and checked FastAPI `0.143.2`, Uvicorn `0.54.0`, Pydantic `2.14.1`, and HTTPX `0.28.1` in `.venv`; exact pins are in `requirements.txt`. HTTPX is needed by FastAPI's `TestClient`; Pydantic is used directly by request/response models.

FastAPI design checked against its official docs for [response models](https://fastapi.tiangolo.com/tutorial/response-model/), [testing](https://fastapi.tiangolo.com/tutorial/testing/), [CORS](https://fastapi.tiangolo.com/tutorial/cors/), and [security tokens](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/). The app will use its own opaque session token rather than JWT so logout can revoke a session without adding a JWT dependency.

## Phase 3

- The React/Vite application lives in `frontend/`, uses React Router declarative routes, and sends browser requests through a same-origin `/api` Vite proxy to FastAPI.
- `frontend/src/api/schema.d.ts` is generated from `frontend/openapi.json` with `openapi-typescript`. `frontend/src/api/routes.ts` centralizes endpoint builders and checks each HTTP method/path against generated OpenAPI route types at compile time.
- Exact package versions are recorded in `frontend/package-lock.json`. Vite proxy and React Router APIs were checked against the official [Vite server proxy](https://vite.dev/config/server-options) and [React Router declarative routing](https://reactrouter.com/start/declarative/routing) docs.
- Playwright Test was added for the browser flow. Its install, browser, and web-first assertion patterns were checked against the official [Playwright installation](https://playwright.dev/docs/intro), [browser install](https://playwright.dev/docs/browsers), and [assertion](https://playwright.dev/docs/test-assertions) docs.
- The client does not request API keys. Provider/model preferences may be set in the UI; credentials remain server environment configuration. Fonts use local system stacks so the client has no third-party runtime asset dependency.
