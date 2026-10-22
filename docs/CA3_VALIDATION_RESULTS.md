# CA-3 Validation Results

## Method

Command: `.\.venv\Scripts\python.exe scripts\validate_samples.py` with `TESTPILOT_ALLOW_UNSAFE_LOCAL=1` and `TESTPILOT_DATA_DIR` pointed at a disposable temporary directory. The script calls the production `Orchestrator.start` path for each sample and its companion requirements file. Settings: deterministic engine, no LLM, local trusted execution, 14 generated tests per function maximum, 5-second per-test timeout, 60-second total timeout, two maximum iterations. Elapsed time is wall-clock time measured around each orchestrator call.

Local trusted execution is not a sandbox. The runs only executed the three bundled repository samples. No external code was used. Counts below are from the actual run records in [ca3_validation_results.json](ca3_validation_results.json); “errors” are separate from failed assertions.

## Results

| Sample | Generated/executed cases | Before fix (pass / fail / error) | After fix (pass / fail / error) | Fix status | Regression rejections | Findings | Elapsed |
|---|---:|---:|---:|---|---:|---:|---:|
| `sample_buggy.py` | 84 | 77 / 6 / 1 | 82 / 1 / 1 | verified; 3 fixes | 0 | 9 | 6.953 s |
| `sample_inventory.py` | 135 | 123 / 9 / 3 | 131 / 1 / 3 | verified; 7 fixes | 0 | 16 | 8.728 s |
| `sample_shop.py` | 26 | 23 / 3 / 0 | 26 / 0 / 0 | verified; 2 fixes | 0 | 3 | 5.593 s |
| **Total** | **245** | **223 / 18 / 4** | **239 / 2 / 4** | **12 accepted fixes** | **0** | **28** | **21.274 s** |

All three fixes were marked verified and reported zero regressions. No proposal was rejected specifically for breaking a previously passing case (regression rejections: 0); proposals not covered by a safe built-in rule were skipped as unresolved logic. The two remaining failed assertions after fixes are on `sample_buggy.py` and `sample_inventory.py`; four errors remained across those runs. The verified patcher improved passing outcomes from 223 to 239 among the same generated cases. That is evidence for these samples only, not a generalized accuracy measure.

The built-in engine accepted deterministic rules for observed division by zero, mutable defaults, bare `except`, `== None`, percentage division, and 1-based pagination. It did not resolve every confirmed requirement failure. Where the engine had no safe applicable rule, findings were retained and described as unresolved rather than silently changed. The AI provider was disabled.

## Phase 2.5: quality metrics

The same three repository samples were run through Fix followed by Improve with local trusted execution. These measured outputs are also saved in `docs/ca3_validation_results.json`. The deterministic quality stage found one safe formatting improvement. “No safe edits” reports unchanged baseline metrics and makes no improvement claim.

| Sample | Improve status | Ruff issues before → after | Needs formatting before → after | AST complexity before → after | Functions before → after | Verification |
|---|---|---:|---:|---:|---:|---|
| `sample_buggy.py` | No safe edits | 0 → 0 | 0 → 0 | 14 → 14 | 6 → 6 | Fix verified; no quality edits |
| `sample_inventory.py` | Verified | 1 → 1 | 1 → 0 | 23 → 23 | 15 → 15 | Same cases rerun; no regression |
| `sample_shop.py` | No safe edits | 0 → 0 | 0 → 0 | 6 → 6 | 5 → 5 | Fix verified; no quality edits |

The Ruff issue count for `sample_inventory.py` is unchanged because its safe improvement was standard formatting; the formatter check changed from needing edits to clean. Unchanged rows are intentionally reported as “no safe edits.”

## Limitations

- The sample set is three small, intentionally buggy modules and is not representative of arbitrary software.
- Expected-value assertions came from sample requirement files and doctests. Generated robustness checks use heuristic oracles and can flag behavior that needs human review.
- Local trusted mode has no filesystem or network isolation. Production use should keep Docker mode enabled; Docker was not involved in these three measurements.
- A passing generated suite is not proof that code is defect-free. Only the tested inputs and assertions are evidenced.
- No external model or account was used, so these results do not evaluate AI-assisted repairs.

## Future scope

Evaluate a larger independently selected module corpus and run it under Docker on supported hosts. Further work could add CI integration for pull-request feedback, MCP-backed tools where a concrete integration is useful, support for additional languages, and an optional LangGraph implementation compared against the existing bounded orchestrator. Each extension should preserve explicit oracle labels and regression-checked patches.
