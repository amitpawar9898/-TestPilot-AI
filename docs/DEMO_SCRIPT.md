# Five-minute CA-3 Demo Script

## Before the demo

- Start the backend and frontend using the development instructions in the root README (Phase 6 will provide `run_dev.ps1`).
- Open the React application in a clean browser session. Keep `sample/sample_buggy.py` and `sample/sample_requirements.txt` available.
- Use the built-in deterministic engine. For the demo, run only bundled repository code in the configured safe environment. Do not switch to local trusted mode for arbitrary code.

## Click-by-click walkthrough (about five minutes)

| Time | Action | Narration / evidence |
|---|---|---|
| 0:00–0:30 | Open the home page, **Fix my code**. | Introduce the problem: quickly inspect Python behavior and edge cases while keeping expected values and execution status explicit. |
| 0:30–1:00 | Choose **Load sample with planted bugs** (or upload `sample_buggy.py` and add its requirements). | Point out that requirements are optional but provide confirmed expected-value tests when included. |
| 1:00–1:45 | Click **Fix my code** / run analysis and wait for completion. | Show the run status and agent workflow. Explain the seven agents and bounded follow-up loop. |
| 1:45–2:30 | Open **Tests** and **Bugs** (or the run's tabs). | Show generated cases and findings. Distinguish requirement/doctest oracles from heuristic no-crash/type checks. |
| 2:30–3:15 | Open **Auto-Fix** and inspect the patch diff. | Explain that deterministic edits are re-tested against the same cases and accepted only when prior passes remain passing and the relevant failures improve. |
| 3:15–4:00 | Open **Report** and download JSON/HTML or tests. | Show the report's outcomes, finding evidence, and execution limits. Explain that unexecuted tests cannot be marked passed. |
| 4:00–5:00 | Open **Agents** and **History**, then wrap on the measured results slide. | Show per-agent events/tool calls and the persisted run. Summarize the fresh three-sample results and state that they are not a general accuracy benchmark. |

## If live execution is unavailable

Use the saved screenshots in `docs/screenshots/` and the measured counts in `docs/CA3_VALIDATION_RESULTS.md`. Explain that those sample measurements used local trusted execution on bundled code and that local trusted mode is not a sandbox. Do not imply that a screenshot or generated case means a test was executed.

## Likely evaluator questions

1. **Why use agents?** The workflow has separable jobs with explicit inputs and outputs: plan, parse, generate cases, execute, judge evidence, repair, report. The orchestrator controls transitions and records each tool call.
2. **Are these agents concurrent or autonomous?** No. They are registered agents called sequentially by a bounded orchestrator. The Critic can trigger a limited extra evidence cycle.
3. **How does the system know the correct answer?** Requirements and doctests provide confirmed expected values. Other generated cases use heuristic properties and are labeled accordingly.
4. **Does a passing run prove the program has no bugs?** No. It proves only that the executed cases passed. Reports include oracle and execution limitations.
5. **How are fixes checked?** The fixer runs the same executed cases on the candidate source. A previously passing case that fails causes rejection; smart/AI changes also need to reduce the failure count.
6. **Is submitted code sandboxed?** Docker is the recommended mode. Local trusted mode has no filesystem or network isolation; the three sample measurements used only bundled repository code in that mode.
7. **What happens without Docker?** Execution stays blocked/not-run unless the administrator explicitly enables local trusted mode. Unexecuted tests are never counted as passed.
8. **Why FastAPI and React?** The JSON contract separates client and service, gives explicit schemas, supports browser and non-browser clients, and allows route/type coverage checks.
9. **What do the sample results show?** On the three bundled modules, 245 cases moved from 223 to 239 passing after 12 verified fixes, with zero reported regressions. Two failed assertions and four errors remained; this is a small demonstration set, not a general accuracy estimate.
10. **What would you improve next?** Validate against a larger independent corpus, run the evaluation in Docker on supported systems, and collect human-reviewed outcomes for heuristic findings.

## Evidence references

- [Architecture](ARCHITECTURE.md)
- [Agent design](CA3_AGENTS_AND_ORCHESTRATION.md)
- [Measured results](CA3_VALIDATION_RESULTS.md)
- [Endpoint verification](ENDPOINT_VERIFICATION.md)
