# Endpoint verification

Live OpenAPI verification: **55/55 operations**; missing=0, extra=0.
Every JSON response body was validated against its OpenAPI response schema. The verifier used a disposable database and synthetic Python code.
The endpoint test server was configured for explicitly trusted local execution of synthetic test fixtures so the fix artifact endpoints could be exercised.
The browser E2E column is filled by `frontend/e2e/workflow.spec.ts`; endpoint rows verified only by HTTP are marked `script`.

| Method | Endpoint | Status | Response schema | Verified by |
|---|---|---:|---|---|
| GET | `/api/health` | 200 | valid | script + e2e |
| POST | `/api/auth/register` | 201 | valid | script + e2e |
| POST | `/api/auth/login` | 200 | valid | script + e2e |
| GET | `/api/auth/me` | 200 | valid | script + e2e |
| GET | `/api/auth/admin/users` | 200 | valid | script |
| POST | `/api/auth/admin/users` | 201 | valid | script |
| PATCH | `/api/auth/admin/users/{user_id}` | 200 | valid | script |
| DELETE | `/api/auth/admin/users/{user_id}` | 200 | valid | script |
| GET | `/api/auth/admin/settings` | 200 | valid | script |
| PATCH | `/api/auth/admin/settings` | 200 | valid | script |
| POST | `/api/auth/logout` | 200 | valid | script + e2e |
| POST | `/api/auth/password` | 200 | valid | script |
| GET | `/api/dashboard` | 200 | valid | script + e2e |
| POST | `/api/analysis/preview` | 200 | valid | script |
| GET | `/api/samples` | 200 | valid | script + e2e |
| GET | `/api/samples/{name}` | 200 | valid | script + e2e |
| GET | `/api/settings` | 200 | valid | script + e2e |
| PATCH | `/api/settings` | 200 | valid | script |
| GET | `/api/settings/models` | 200 | valid | script |
| GET | `/api/settings/availability` | 200 | valid | script |
| POST | `/api/settings/test-connection` | 200 | valid | script |
| POST | `/api/runs` | 202 | valid | script + e2e |
| GET | `/api/runs/{run_id}/status` | 200 | valid | script + e2e |
| GET | `/api/runs` | 200 | valid | script |
| GET | `/api/runs/{run_id}/events` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}` | 200 | valid | script |
| GET | `/api/runs/{run_id}/analysis` | 200 | valid | script |
| GET | `/api/runs/{run_id}/findings` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/tests` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/fix` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/fix/diff` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/report` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/tests/download` | 200 | valid | script |
| POST | `/api/runs/{run_id}/tests` | 200 | valid | script |
| PATCH | `/api/runs/{run_id}/tests` | 200 | valid | script |
| POST | `/api/runs/{run_id}/execution` | 202 | valid | script |
| GET | `/api/runs/{run_id}/fix/download` | 200 | valid | script + e2e |
| GET | `/api/runs/{run_id}/improvement/download` | 200 | valid | script + e2e |
| POST | `/api/runs/{run_id}/fix/retry` | 202 | valid | script |
| GET | `/api/runs/{run_id}/report/download` | 200 | valid | script |
| POST | `/api/runs/{run_id}/report/save` | 200 | valid | script |
| GET | `/api/learning` | 200 | valid | script |
| PATCH | `/api/learning/{fix_id}` | 200 | valid | script |
| PATCH | `/api/learning/run/{run_id}` | 200 | valid | script |
| GET | `/api/learning/export` | 200 | valid | script |
| DELETE | `/api/learning/{fix_id}` | 200 | valid | script |
| DELETE | `/api/learning` | 200 | valid | script |
| POST | `/api/batch` | 202 | valid | script |
| GET | `/api/batch/{batch_id}` | 200 | valid | script |
| GET | `/api/batch/{batch_id}/download` | 200 | valid | script |
| POST | `/api/settings/retention/purge` | 200 | valid | script |
| POST | `/api/settings/retention/scrub-sources` | 200 | valid | script |
| DELETE | `/api/runs/{run_id}` | 200 | valid | script |
| DELETE | `/api/auth/account/runs` | 200 | valid | script |
| POST | `/api/auth/account/delete` | 200 | valid | script |

Machine-readable results: [`endpoint_verification.json`](endpoint_verification.json).
