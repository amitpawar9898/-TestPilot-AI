from tools.code_analyzer import analyze_source

SRC = '''
import os
def avg(xs, d=[]):
    """Mean."""
    return sum(xs) / len(xs)

def risky(x):
    try:
        return eval(x)
    except:
        pass
'''


def test_syntax_error_is_reported_with_location():
    a = analyze_source("def f(:\n    pass\n")
    assert not a["ok"]
    assert a["syntax_error"]["line"] == 1
    assert a["findings"][0]["severity"] == "Critical"


def test_functions_and_params_extracted():
    a = analyze_source(SRC, use_ruff=False)
    f = {x["name"]: x for x in a["functions"]}
    assert [p["name"] for p in f["avg"]["params"]] == ["xs", "d"]
    assert f["avg"]["params"][1]["has_default"]
    assert f["avg"]["lineno"] == 3


def test_static_findings_have_severity_and_lines():
    a = analyze_source(SRC, use_ruff=False)
    rules = {x["rule"]: x for x in a["findings"]}
    assert rules["TP001"]["severity"] == "Medium"  # mutable default
    assert rules["TP004"]["severity"] == "High"  # eval
    assert "TP002" in rules and "TP006" in rules
    assert all(x["line"] for x in a["findings"])


def test_source_is_not_executed():
    a = analyze_source("raise SystemExit('should never run')\ndef f(): pass\n", use_ruff=False)
    assert a["ok"] and len(a["functions"]) == 1


def test_side_effect_functions_are_flagged():
    a = analyze_source("import os\ndef rm(p):\n    os.remove(p)\n", use_ruff=False)
    assert a["functions"][0]["unsafe_to_autotest"]
