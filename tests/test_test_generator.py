from tools.code_analyzer import analyze_source
from tools.test_generator import assign_ids, export_pytest, generate_cases, parse_expect, validate_case

SRC = '''
def add(a: int, b: int) -> int:
    """Add.
    >>> add(2, 3)
    5
    """
    return a + b

def rm(p):
    import os
    os.remove(p)
'''


def _gen(req="", targets=None, **kw):
    a = analyze_source(SRC, use_ruff=False)
    cases, warns, skipped = generate_cases(a, req, targets or [], ["normal", "boundary", "edge", "negative"], 12, **kw)
    return assign_ids(cases, [], 1), warns, skipped


def test_requirement_and_doctest_oracles():
    cases, warns, _ = _gen("add(1, 2) == 3\nadd(1,2) raises ValueError")
    oracles = {(c["oracle"], c["expect"]["type"]) for c in cases}
    assert ("requirement", "equals") in oracles and ("requirement", "raises") in oracles and ("doctest", "equals") in oracles


def test_all_categories_present_and_valid():
    cases, _, _ = _gen(targets=["add"])
    assert {"normal", "boundary", "edge", "negative"} <= {c["category"] for c in cases}
    assert all(validate_case(c, {"add"}) == [] for c in cases)


def test_side_effect_function_skipped_with_reason():
    _, _, skipped = _gen()
    assert any(s["name"] == "rm" for s in skipped)


def test_unknown_target_warns():
    cases, warns, _ = _gen(targets=["nope"])
    assert not [c for c in cases if c["function"] == "nope"] and any("not found" in w for w in warns)


def test_bad_requirement_value_is_rejected_not_executed():
    cases, warns, _ = _gen("add(1, 2) == __import__('os').getcwd()")
    assert not [c for c in cases if c["source"] == "requirement"] and warns


def test_exported_pytest_is_valid_python():
    cases, _, _ = _gen("add(1, 2) == 3")
    compile(export_pytest(cases, "submitted"), "t.py", "exec")


def test_expect_text_roundtrip():
    assert parse_expect("== [1, 2]")["type"] == "equals"
    assert parse_expect("raises ValueError")["exception"] == "ValueError"
