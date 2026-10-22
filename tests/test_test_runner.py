from config.settings import Settings
from tools.test_runner import run_cases, sandbox_status

SRC = "def add(a,b): return a+b\ndef boom(x): return 1/x\ndef loop():\n    while True: pass\n"


def _c(i, fn, args, expect):
    return {"id": i, "function": fn, "args": args, "kwargs": "{}", "expect": expect}


def _run(cases, **kw):
    return run_cases(SRC, cases, Settings(execution_mode="local_trusted", per_test_timeout=1, total_timeout=15, **kw))


def test_pass_fail_error_timeout_are_distinguished():
    r = _run([_c("a", "add", "(1, 2)", {"type": "equals", "value": "3"}), _c("b", "add", "(1, 2)", {"type": "equals", "value": "4"}),
              _c("c", "boom", "(0,)", {"type": "no_exception"}), _c("d", "loop", "()", {"type": "no_exception"})])
    assert [x["status"] for x in r["results"]] == ["passed", "failed", "error", "timeout"]
    assert r["results"][2]["line"] == 2


def test_wrong_exception_is_a_failure_not_a_pass():
    r = _run([_c("a", "boom", "(0,)", {"type": "raises", "exception": "ValueError"})])
    assert r["results"][0]["status"] == "failed"


def test_import_failure_marks_all_cases_error():
    r = run_cases("def f(:\n", [_c("a", "f", "()", {"type": "no_exception"})], Settings(execution_mode="local_trusted"))
    assert r["results"][0]["status"] == "error"


def test_disabled_mode_executes_nothing(monkeypatch=None):
    s = Settings(execution_mode="disabled")
    r = run_cases(SRC, [_c("a", "add", "(1, 2)", {"type": "no_exception"})], s)
    assert not r["available"] and r["results"] == []


def test_unsafe_local_requires_opt_in():
    import os
    old = os.environ.pop("TESTPILOT_ALLOW_UNSAFE_LOCAL")
    try:
        assert not sandbox_status(Settings(execution_mode="local_trusted"))["available"]
    finally:
        os.environ["TESTPILOT_ALLOW_UNSAFE_LOCAL"] = old


def test_api_keys_not_visible_to_code_under_test():
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-secret-123456"
    try:
        r = run_cases("import os\ndef k(): return os.environ.get('ANTHROPIC_API_KEY')\n", [_c("a", "k", "()", {"type": "equals", "value": "None"})],
                      Settings(execution_mode="local_trusted"))
        assert r["results"][0]["status"] == "passed"
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_invalid_case_literal_is_rejected():
    r = _run([_c("a", "add", "(__import__('os'),)", {"type": "no_exception"})])
    assert r["results"][0]["status"] == "error" and "Invalid test definition" in r["results"][0]["detail"]
