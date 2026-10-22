"""Run the three bundled samples through the real deterministic pipeline.

This intentionally executes only trusted repository samples in local mode. It
uses a disposable SQLite database and writes measured results to docs/.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TESTPILOT_DATA_DIR", str(ROOT / "reports" / "ca3-validation-data"))

from config.settings import Settings  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from core.state import RunRequest  # noqa: E402
from database.db import Database  # noqa: E402


def counts(results: list[dict]) -> dict[str, int]:
    keys = ("passed", "failed", "error", "timeout", "not_run")
    return {key: sum(r.get("status") == key for r in results) for key in keys}


def main() -> None:
    if os.environ.get("TESTPILOT_ALLOW_UNSAFE_LOCAL") != "1":
        raise SystemExit("Set TESTPILOT_ALLOW_UNSAFE_LOCAL=1 to run the trusted bundled samples.")
    data = Path(os.environ["TESTPILOT_DATA_DIR"])
    db = Database(data / "testpilot.db")
    settings = Settings(execution_mode="local_trusted", total_timeout=60, per_test_timeout=5,
                        max_tests_per_function=14, max_iterations=2, retain_source=False)
    pairs = [
        ("sample_buggy.py", "sample_requirements.txt"),
        ("sample_inventory.py", "sample_inventory_requirements.txt"),
        ("sample_shop.py", "sample_shop_requirements.txt"),
    ]
    records = []
    for filename, requirements_file in pairs:
        code = (ROOT / "sample" / filename).read_text(encoding="utf-8")
        requirements = (ROOT / "sample" / requirements_file).read_text(encoding="utf-8")
        request = RunRequest(code=code, filename=filename, requirements=requirements, auto_fix=True,
                             fix_mode="safe", max_tests_per_function=14)
        start = time.perf_counter()
        state = Orchestrator(settings, db).start(request)
        elapsed = time.perf_counter() - start
        fix = state.fix or {}
        improvement = state.improvement or {}
        before = fix.get("before") or counts(state.results)
        after = fix.get("after") or counts(state.results)
        records.append({
            "sample": filename,
            "requirements_file": requirements_file,
            "run_id": state.run_id,
            "status": state.status,
            "verdict": state.verdict,
            "duration_seconds": round(elapsed, 3),
            "generated_cases": len(state.cases),
            "results_before_fix": before,
            "results_after_fix": after,
            "fix_status": fix.get("status", "none"),
            "applied_fixes": fix.get("applied", []),
            "skipped_fixes": fix.get("skipped", []),
            "regressions": fix.get("regressions", []),
            "improvement_status": improvement.get("status", "skipped"),
            "improvement_reason": improvement.get("reason", ""),
            "improvement_metrics_before": improvement.get("before_metrics", {}),
            "improvement_metrics_after": improvement.get("after_metrics", {}),
            "improvement_changes": improvement.get("applied", []),
            "improvement_verification": improvement.get("verification", {}),
            "regression_rejections": sum(
                "broke previously passing tests" in str(item.get("reason", "")).lower()
                or "break previously passing tests" in str(item.get("reason", "")).lower()
                for item in fix.get("skipped", [])),
            "findings": len(state.findings),
            "severity_counts": {severity: sum(f.get("severity") == severity for f in state.findings)
                                for severity in ("Critical", "High", "Medium", "Low", "Info")},
            "execution_mode": state.isolation,
            "llm_used": state.llm_used,
        })
        print(json.dumps(records[-1], ensure_ascii=False))
    out = {"method": "real Orchestrator.start; deterministic provider; trusted local execution of bundled samples",
           "settings": {"max_tests_per_function": 14, "total_timeout_seconds": 60, "per_test_timeout_seconds": 5,
                        "max_iterations": 2, "llm": "disabled"}, "samples": records}
    target = ROOT / "docs" / "ca3_validation_results.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
