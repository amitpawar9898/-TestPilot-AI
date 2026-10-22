"""Test-case generation. Cases are plain data (JSON-able dicts), so they can be edited, stored, executed in a
sandbox and exported as pytest code.

Oracles (how trustworthy an expectation is):
  requirement  - user-written example (`f(1, 2) == 3`)         -> confirmed oracle
  doctest      - `>>> f(1)` example from a docstring            -> confirmed oracle
  user_edit    - expectation typed by the user in the UI        -> confirmed oracle
  llm_unverified - expectation proposed by an LLM               -> needs human review
  heuristic    - smoke / robustness / type / determinism checks -> cannot prove functional correctness
"""
from __future__ import annotations

import ast
import json
import re

from tools.harness import parse_args, parse_kwargs, safe_literal

CATEGORIES = ["normal", "boundary", "edge", "negative", "requirement"]
CONFIRMED_ORACLES = {"requirement", "doctest", "user_edit"}

POOLS: dict[str, dict[str, list]] = {
    "int": {"normal": [3, 7], "boundary": [0, 1, -1], "edge": [2**31 - 1, -(2**31)], "negative": [None, "abc"], "extra": [-7, 100, 10**6, 2]},
    "float": {"normal": [2.5, 10.75], "boundary": [0.0, 1.0, -1.0], "edge": [1e-9, 1e12], "negative": [None, "abc"], "extra": [-2.5, 0.1, 3e8]},
    "number": {"normal": [3, 2.5], "boundary": [0, -1, 1.0], "edge": [10**12], "negative": [None, "abc"], "extra": [-7, 0.1, 100]},
    "str": {"normal": ["hello", "Test String"], "boundary": ["", "a"], "edge": ["   ", "x" * 500, "héllo wörld 🚀"], "negative": [None, 123],
            "extra": ["A B  C", "line1\nline2", "12345", "!@#$%^&*()"]},
    "bool": {"normal": [True, False], "boundary": [False], "edge": [], "negative": [None, "yes"], "extra": []},
    "list": {"normal": [[1, 2, 3], [5, 3, 8, 1]], "boundary": [[], [1]], "edge": [[0, 0, 0], [-1, -5, 3], [10**6, -(10**6)]],
             "negative": [None, 123], "extra": [[2, 2, 2], [-3, -2, -1], [1] * 1000, [3, 1, 2, 3, 1]]},
    "list_str": {"normal": [["a", "b", "c"], ["apple", "Banana"]], "boundary": [[], [""]], "edge": [["", ""], ["a"] * 100, ["é", "🚀"]],
                 "negative": [None, 123], "extra": [["z", "a", "m"], ["a", "a"]]},
    "list_float": {"normal": [[1.5, 2.5, 3.0], [0.1, 0.2]], "boundary": [[], [0.0]], "edge": [[1e12, -1e12], [0.0, 0.0]],
                   "negative": [None, 123], "extra": [[-1.5, -2.5], [1e-9]]},
    "dict": {"normal": [{"a": 1, "b": 2}], "boundary": [{}], "edge": [{"": 0}, {"k": None}], "negative": [None, "abc"], "extra": [{"a": {"b": 1}}]},
    "tuple": {"normal": [(1, 2, 3)], "boundary": [()], "edge": [(None,)], "negative": [None, "abc"], "extra": [(1,)]},
    "any": {"normal": [3], "boundary": [0, ""], "edge": [None], "negative": [], "extra": ["abc", [1, 2]]},
}
NAME_KINDS = {
    "int": {"n", "i", "j", "k", "count", "num", "size", "length", "index", "idx", "limit", "years", "age", "days", "year", "quantity", "qty", "number", "steps"},
    "float": {"price", "amount", "rate", "percent", "percentage", "ratio", "weight", "height", "balance", "salary", "discount", "tax", "temperature"},
    "str": {"name", "text", "s", "string", "word", "sentence", "title", "email", "message", "line", "label", "prefix", "suffix", "query", "phrase"},
    "list": {"items", "lst", "nums", "numbers", "values", "data", "array", "arr", "elements", "scores", "xs", "seq", "sequence"},
    "list_str": {"words", "names", "tokens", "lines", "strings"},
    "dict": {"d", "mapping", "config", "record", "params", "options"},
    "bool": {"flag", "enabled", "verbose", "reverse", "strict"},
}
PRIMITIVE_RETURNS = {"int", "float", "str", "bool", "list", "dict", "tuple", "set"}
REQ_RE = re.compile(
    r"^\s*(?:[-*\d.)]+\s+)?(?P<call>[A-Za-z_]\w*\s*\(.*\))(?P<op>\s*(?:==|->|=>|→)\s*|\s+(?:should\s+return|returns|should\s+raise|raises|should\s+throw|throws)\s+)(?P<rhs>.+?)\s*$")


# ----------------------------------------------------------------- helpers
def lit(v) -> str:
    if isinstance(v, (str, list)) and len(v) > 40 and len({repr(x) for x in v}) == 1:
        return f"{repr(v[:1])} * {len(v)}"
    return repr(v)


def format_expect(e: dict) -> str:
    t = e.get("type")
    return {"equals": f"== {e.get('value')}", "raises": f"raises {e.get('exception')}", "no_exception": "no exception",
            "returns_type": f"type {e.get('name')}", "deterministic": "deterministic", "robust": "robust"}.get(t, str(e))


def parse_expect(text: str) -> dict:
    t = (text or "").strip()
    low = t.lower()
    if low.startswith("=="):
        v = t[2:].strip()
        safe_literal(v)
        return {"type": "equals", "value": v}
    if low.startswith("raises "):
        name = t[7:].strip().split(".")[-1]
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError("exception name must be a valid identifier")
        return {"type": "raises", "exception": name}
    if low.startswith("type "):
        return {"type": "returns_type", "name": t[5:].strip()}
    if low in ("no exception", "no_exception"):
        return {"type": "no_exception"}
    if low == "deterministic":
        return {"type": "deterministic"}
    if low == "robust":
        return {"type": "robust"}
    raise ValueError("Expected one of: '== <value>', 'raises <Exception>', 'type <name>', 'no exception', 'deterministic', 'robust'")


def validate_case(case: dict, known_functions: set[str] | None = None) -> list[str]:
    errs = []
    if not case.get("function"):
        errs.append("function is required")
    elif known_functions is not None and case["function"] not in known_functions:
        errs.append(f"unknown function '{case['function']}'")
    try:
        parse_args(case.get("args") or "()")
    except Exception as e:  # noqa: BLE001
        errs.append(f"args: {e}")
    try:
        parse_kwargs(case.get("kwargs") or "{}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"kwargs: {e}")
    try:
        parse_args(case.get("ctor") or "()")
    except Exception as e:  # noqa: BLE001
        errs.append(f"constructor args: {e}")
    e = case.get("expect") or {}
    if e.get("type") == "equals":
        try:
            safe_literal(e.get("value", ""))
        except Exception as ex:  # noqa: BLE001
            errs.append(f"expected value: {ex}")
    elif e.get("type") not in ("raises", "no_exception", "returns_type", "deterministic", "robust"):
        errs.append("unknown expectation")
    return errs


def case_key(c: dict) -> tuple:
    return (c.get("function"), c.get("ctor") or "()", json.dumps(c.get("setup") or []), c.get("args"), c.get("kwargs"), json.dumps(c.get("expect"), sort_keys=True))


def _call_text(args: str, kwargs: str) -> str:
    a = parse_args(args or "()")
    parts = [lit(x) if not isinstance(x, float) or x == x else "float('nan')" for x in a]
    return ", ".join(parts + [f"{k}={lit(v)}" for k, v in parse_kwargs(kwargs or "{}").items()])


def call_repr(c: dict) -> str:
    """Human-readable call, including the constructor for methods: Cart().add(1)."""
    try:
        args = _call_text(c.get("args"), c.get("kwargs"))
        if "." in c["function"]:
            cls, meth = c["function"].split(".", 1)
            steps = "".join(f".{s['m']}({_call_text(s['args'], s['kwargs'])})" for s in c.get("setup") or [])
            return f"{cls}({_call_text(c.get('ctor') or '()', '{}')}){steps}.{meth}({args})"
        return f"{c['function']}({args})"
    except Exception:  # noqa: BLE001
        return f"{c.get('function')}{c.get('args')}"


# --------------------------------------------------------- type inference
def _kind_from_annotation(a: str | None) -> str | None:
    if not a:
        return None
    a = a.replace(" ", "")
    m = re.match(r"(?:typing\.)?Optional\[(.+)\]$", a)
    if m:
        a = m.group(1)
    a = re.sub(r"\|None$|^None\|", "", a)
    low = a.lower()
    if low.startswith(("list[str", "sequence[str", "iterable[str", "set[str")):
        return "list_str"
    if low.startswith(("list[float", "sequence[float", "iterable[float")):
        return "list_float"
    if low.startswith(("list", "sequence", "iterable", "set", "frozenset", "deque", "collections.")):
        return "list"
    if low.startswith(("dict", "mapping", "defaultdict")):
        return "dict"
    if low.startswith("tuple"):
        return "tuple"
    if low == "bool":
        return "bool"
    if "float" in low and "int" in low:
        return "number"
    if low.startswith("int"):
        return "int"
    if low.startswith("float"):
        return "float"
    if low.startswith("str"):
        return "str"
    if low in ("any", "object"):
        return "any"
    return None


def infer_kind(fn: dict, p: dict) -> tuple[str, str]:
    """Return (kind, source-of-inference)."""
    k = _kind_from_annotation(p.get("annotation"))
    if k:
        return k, "annotation"
    if p.get("has_default") and p.get("default") not in (None, "None"):
        try:
            v = ast.literal_eval(p["default"])
            for t, kind in ((bool, "bool"), (int, "int"), (float, "float"), (str, "str"), (list, "list"), (dict, "dict"), (tuple, "tuple")):
                if isinstance(v, t):
                    return kind, "default value"
        except Exception:  # noqa: BLE001
            pass
    hints = set(fn.get("param_hints", {}).get(p["name"], []))
    for h, kind in (("str", "str"), ("list", "list"), ("dict", "dict"), ("float", "float"), ("int", "int")):
        if h in hints:
            return kind, "body usage"
    if hints & {"sized", "iterable", "indexable"}:
        return "list", "body usage"
    if "number" in hints:
        return "number", "body usage"
    name = p["name"].lower()
    for kind, names in NAME_KINDS.items():
        if name in names or any(name.endswith("_" + n) for n in names):
            return kind, "parameter name"
    return "any", "unknown"


# ------------------------------------------------------ deterministic gen
def _positional(fn):
    return [p for p in fn["params"] if p["kind"] in ("positional", "keyword_only") and p["name"] not in ("self", "cls")]


def _build(params, values: dict, omit_defaults=False):
    args, kwargs = [], {}
    for p in params:
        if omit_defaults and p["has_default"]:
            continue
        if p["name"] not in values:
            continue
        if p["kind"] == "keyword_only":
            kwargs[p["name"]] = values[p["name"]]
        else:
            args.append(values[p["name"]])
    a = "(" + ", ".join(lit(x) for x in args) + ("," if len(args) == 1 else "") + ")"
    k = "{" + ", ".join(f"{n!r}: {lit(v)}" for n, v in kwargs.items()) + "}"
    return a, k


def _mk(fn, category, label, args, kwargs, expect, why, source="deterministic", oracle="heuristic"):
    return {"function": fn["name"], "name": f"{fn['name']}: {label}", "category": category, "args": args, "kwargs": kwargs,
            "expect": expect, "rationale": why, "source": source, "oracle": oracle, "enabled": True}


def _function_cases(fn: dict, categories: set[str], cap: int, stress: bool) -> tuple[list[dict], list[str]]:
    params = _positional(fn)
    notes: list[str] = []
    kinds = {p["name"]: infer_kind(fn, p) for p in params}
    weak = [n for n, (k, src) in kinds.items() if src == "unknown"]
    if weak:
        notes.append(f"{fn['name']}: parameter types for {', '.join(weak)} could not be inferred; inputs may not suit the function.")
    pool = lambda n, key: POOLS[kinds[n][0]][key]  # noqa: E731
    base = {n: pool(n, "normal")[0] for n in kinds}
    by_cat: dict[str, list[dict]] = {c: [] for c in ("normal", "boundary", "edge", "negative")}
    a, k = _build(params, base)
    by_cat["normal"].append(_mk(fn, "normal", "typical input", a, k, {"type": "no_exception"},
                                 "Baseline: a typical valid input must not raise an exception."))
    base2 = {n: (pool(n, "normal")[1] if len(pool(n, "normal")) > 1 else pool(n, "normal")[0]) for n in kinds}
    if base2 != base:
        a2, k2 = _build(params, base2)
        by_cat["normal"].append(_mk(fn, "normal", "second typical input", a2, k2, {"type": "no_exception"},
                                     "A different valid input guards against code that only works for one value."))
    ret = _kind_from_annotation(fn.get("returns"))
    if ret in PRIMITIVE_RETURNS or ret in ("list_str", "list_float"):
        want = "list" if ret.startswith("list") else ret
        by_cat["normal"].append(_mk(fn, "normal", f"returns {want}", a, k, {"type": "returns_type", "name": want},
                                     f"The annotation promises `{fn['returns']}`; verify the real return type matches."))
    if any(p["has_default"] for p in params):
        ad, kd = _build(params, base, omit_defaults=True)
        by_cat["normal"].append(_mk(fn, "normal", "default arguments", ad, kd, {"type": "no_exception"},
                                     "Calling with only the required arguments exercises the default values."))
    if not fn["nondeterministic"] and not fn["unsafe_to_autotest"]:
        by_cat["normal"].append(_mk(fn, "normal", "deterministic result", a, k, {"type": "deterministic"},
                                     "A pure function must return the same result for the same input."))
    for n in kinds:
        for v in pool(n, "boundary")[:2]:
            aa, kk = _build(params, {**base, n: v})
            by_cat["boundary"].append(_mk(fn, "boundary", f"boundary '{n}' = {lit(v)[:30]}", aa, kk, {"type": "robust"},
                                          f"Boundary values (empty, zero, one, negative one) for '{n}' are where off-by-one and empty-input bugs live. "
                                          "Returning normally or rejecting with a clear ValueError/TypeError is acceptable; any other exception is flagged."))
        for v in pool(n, "edge")[: (2 if stress else 1)]:
            aa, kk = _build(params, {**base, n: v})
            by_cat["edge"].append(_mk(fn, "edge", f"edge '{n}' = {lit(v)[:30]}", aa, kk, {"type": "robust"},
                                      f"Extreme or unusual values for '{n}' (very large, unicode, whitespace-only) probe overflow and encoding handling. "
                                      "A deliberate ValueError/TypeError is acceptable."))
        neg = pool(n, "negative")
        if neg:
            aa, kk = _build(params, {**base, n: neg[0]})
            by_cat["negative"].append(_mk(fn, "negative", f"invalid '{n}' = {lit(neg[0])[:30]}", aa, kk, {"type": "robust"},
                                          f"An invalid '{n}' should be rejected with a clear exception (TypeError/ValueError) or handled, not crash with an unrelated error."))
    chosen: list[dict] = []
    order = [c for c in ("normal", "boundary", "edge", "negative") if c in categories]
    while len(chosen) < cap and any(by_cat[c] for c in order):
        for c in order:
            if by_cat[c] and len(chosen) < cap:
                chosen.append(by_cat[c].pop(0))
    return chosen, notes


def _extra_cases(fn: dict, cap: int) -> list[dict]:
    params = _positional(fn)
    kinds = {p["name"]: infer_kind(fn, p)[0] for p in params}
    base = {n: POOLS[k]["normal"][0] for n, k in kinds.items()}
    out = []
    for n, k in kinds.items():
        for v in POOLS[k]["extra"][:2]:
            a, kw = _build(params, {**base, n: v})
            out.append(_mk(fn, "edge", f"extra input '{n}' = {lit(v)[:30]}", a, kw, {"type": "no_exception"},
                           f"Follow-up round: additional distinct value for '{n}' requested by the Critic to widen coverage."))
    combo_b = {n: (POOLS[k]["boundary"] or POOLS[k]["normal"])[0] for n, k in kinds.items()}
    a, kw = _build(params, combo_b)
    out.append(_mk(fn, "boundary", "all parameters at boundary", a, kw, {"type": "no_exception"},
                   "Boundary values combined across all parameters can trigger interactions single-parameter tests miss."))
    combo_e = {n: (POOLS[k]["edge"] or POOLS[k]["normal"])[0] for n, k in kinds.items()}
    a, kw = _build(params, combo_e)
    out.append(_mk(fn, "edge", "all parameters at extremes", a, kw, {"type": "no_exception"},
                   "Extreme values combined across all parameters."))
    for n, k in kinds.items():
        if len(POOLS[k]["negative"]) > 1:
            a, kw = _build(params, {**base, n: POOLS[k]["negative"][1]})
            out.append(_mk(fn, "negative", f"second invalid type for '{n}'", a, kw, {"type": "robust"},
                           f"A different invalid type for '{n}' checks that validation is not limited to one case."))
    return out[:cap]


# ------------------------------------------------------ examples/oracles
def _lit_tuple(nodes) -> str:
    items = [ast.unparse(a) for a in nodes]
    for v in items:
        safe_literal(v)
    return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"


def _setup_call(text: str) -> dict:
    node = ast.parse(text.strip(), mode="eval").body
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)) or any(isinstance(a, ast.Starred) for a in node.args):
        raise ValueError(f"setup step must look like method(args): {text[:40]}")
    kws = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
    for v in kws.values():
        safe_literal(v)
    return {"m": node.func.id, "args": _lit_tuple(node.args), "kwargs": "{" + ", ".join(f"{n!r}: {v}" for n, v in kws.items()) + "}"}


def setup_text(c: dict) -> str:
    return "; ".join(f"{s['m']}{s['args']}" for s in c.get("setup") or [])


def _call_to_case(call_src: str, funcs: dict[str, dict]):
    """Accepts `f(1)` and `Cls(ctor).method(1)`. Returns (qualified name, args, kwargs, ctor)."""
    tree = ast.parse(call_src.strip(), mode="eval").body
    ctor = "()"
    if (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Attribute) and isinstance(tree.func.value, ast.Call)
            and isinstance(tree.func.value.func, ast.Name)):
        inner = tree.func.value
        if inner.keywords or any(isinstance(a, ast.Starred) for a in inner.args):
            raise ValueError("constructor keyword/star arguments are not supported")
        name = f"{inner.func.id}.{tree.func.attr}"
        ctor = _lit_tuple(inner.args)
    elif isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name):
        name = tree.func.id
    else:
        raise ValueError("not a simple function or method call")
    if name not in funcs:
        raise LookupError(name)
    if any(k.arg is None for k in tree.keywords) or any(isinstance(a, ast.Starred) for a in tree.args):
        raise ValueError("star-arguments are not supported")
    kws = {k.arg: ast.unparse(k.value) for k in tree.keywords}
    for v in kws.values():
        safe_literal(v)
    k = "{" + ", ".join(f"{n!r}: {v}" for n, v in kws.items()) + "}"
    return name, _lit_tuple(tree.args), k, ctor


def parse_requirement_examples(text: str, funcs: dict[str, dict]) -> tuple[list[dict], list[str]]:
    cases, warns = [], []
    for raw in (text or "").replace("`", "").splitlines():
        setup: list[dict] = []
        line = raw
        if " | " in raw:  # Cls(ctor) | step(args) | ... | method(args) == value
            parts = [x.strip() for x in raw.split(" | ")]
            try:
                setup = [_setup_call(x) for x in parts[1:-1]]
            except Exception as e:  # noqa: BLE001
                warns.append(f"Requirement example skipped ({e}): {raw.strip()[:80]}")
                continue
            line = f"{parts[0]}.{parts[-1]}"
        m = REQ_RE.match(line)
        if not m:
            continue
        op, rhs = m.group("op").strip().lower(), m.group("rhs").strip().rstrip(".")
        try:
            name, a, k, ctor = _call_to_case(m.group("call"), funcs)
            if op.startswith(("raises", "should raise", "throws", "should throw")):
                exc = re.match(r"[A-Za-z_][\w.]*", rhs)
                if not exc:
                    raise ValueError("missing exception name")
                expect = {"type": "raises", "exception": exc.group(0).split(".")[-1]}
            else:
                safe_literal(rhs)
                expect = {"type": "equals", "value": rhs}
        except LookupError as e:
            warns.append(f"Requirement example skipped: function '{e}' is not a top-level function in the code: {raw.strip()[:80]}")
            continue
        except Exception as e:  # noqa: BLE001
            warns.append(f"Requirement example skipped ({e}): {raw.strip()[:80]}")
            continue
        c = _mk({**funcs[name], "name": name}, "requirement", f"requirement `{raw.strip()[:60]}`", a, k, expect,
                "Taken directly from your stated requirements, so a failure here is a confirmed behavior mismatch.",
                source="requirement", oracle="requirement")
        c["ctor"] = ctor
        c["setup"] = setup
        cases.append(c)
    return cases, warns


def parse_doctests(fn: dict, funcs: dict[str, dict]) -> list[dict]:
    cases, lines = [], (fn.get("docstring") or "").splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith(">>> "):
            expr, j, out = s[4:], i + 1, []
            while j < len(lines) and lines[j].strip().startswith("... "):
                expr += " " + lines[j].strip()[4:]
                j += 1
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(">>>"):
                out.append(lines[j].strip())
                j += 1
            if out:
                try:
                    name, a, k, ctor = _call_to_case(expr, funcs)
                    safe_literal(" ".join(out))
                    c = _mk({**funcs[name], "name": name}, "requirement", f"doctest `{expr[:50]}`", a, k, {"type": "equals", "value": " ".join(out)},
                            "Example from the function's own docstring; the documented output is the expected result.", source="doctest", oracle="doctest")
                    c["ctor"] = ctor
                    cases.append(c)
                except Exception:  # noqa: BLE001
                    pass
            i = j
        else:
            i += 1
    return cases


# ---------------------------------------------------------------- public
def _class_plan(analysis: dict, cls: dict) -> tuple[str | None, str | None, list[str]]:
    """(ctor literal, skip reason, notes) for a class we can instantiate with guessed arguments."""
    local = {c["name"] for c in analysis["classes"]}
    import builtins
    ext = [b for b in cls["bases"] if b and b.split(".")[0] not in local and not hasattr(builtins, b.split(".")[0])]
    if ext:
        return None, f"inherits from external base class {', '.join(ext)}", []
    init = next((f for f in analysis["functions"] if f["qualname"] == f"{cls['name']}.__init__"), None)
    if init is None:
        return "()", None, []
    if init["unsafe_to_autotest"]:
        return None, "constructor has side effects (" + ", ".join(init["side_effects"]) + ")", []
    params = _positional(init)
    kinds = {p["name"]: infer_kind(init, p) for p in params}
    vals = [POOLS[kinds[p["name"]][0]]["normal"][0] for p in params if p["kind"] == "positional" and not p["has_default"]]
    notes = [f"{cls['name']}: constructor arguments for {', '.join(n for n, (_, s) in kinds.items() if s == 'unknown')} were guessed."] if any(s == "unknown" for _, s in kinds.values()) else []
    return "(" + ", ".join(lit(v) for v in vals) + ("," if len(vals) == 1 else "") + ")", None, notes


def generate_cases(analysis: dict, requirements: str, targets: list[str], categories: list[str], cap: int,
                   stress: bool = False, extra_for: list[str] | None = None, existing: list[dict] | None = None,
                   iteration: int = 1) -> tuple[list[dict], list[str], list[dict]]:
    """Returns (cases-without-ids, warnings, skipped[{name, reason}])."""
    existing = existing or []
    top = {f["name"]: f for f in analysis["functions"] if not f["is_method"]}
    methods = {f["qualname"]: f for f in analysis["functions"] if f["is_method"]}
    warns: list[str] = []
    skipped: list[dict] = []
    tset = set(targets or [])
    selected = [n for n in (targets or top.keys()) if n in top and not top[n]["is_async"]]
    class_targets = {t for t in tset if t in {c["name"] for c in analysis["classes"]}}
    method_targets = {t for t in tset if t in methods}
    missing = [n for n in tset if n not in top and n not in methods and n not in class_targets]
    if missing:
        warns.append(f"Target(s) not found: {', '.join(missing)}. Available: {', '.join(list(top) + list(methods)) or 'none'}.")
    cases: list[dict] = []
    if iteration == 1:
        req, w = parse_requirement_examples(requirements, {**top, **methods})
        warns += w
        cases += [c for c in req if not tset or c["function"] in tset or c["function"].split(".")[0] in tset]
        for n in selected:
            cases += parse_doctests(top[n], {**top, **methods})
        if analysis.get("script_mode") and not analysis.get("functions") and not tset:
            cases.append({"function": "__module__", "name": "script: completes without an unhandled exception",
                          "category": "normal", "args": "()", "kwargs": "{}", "expect": {"type": "no_exception"},
                          "rationale": "Runs the top-level Python script once in the configured isolated runner. This smoke test checks that it completes; move the logic into a callable function and add requirement examples to verify its behavior.",
                          "source": "deterministic", "oracle": "heuristic", "enabled": True})
            warns.append("No callable functions were found. A script smoke test was generated; it does not prove the script's output or logic is correct.")
    for n in selected:
        fn = top[n]
        if extra_for is not None and n not in extra_for:
            continue
        if fn["unsafe_to_autotest"]:
            skipped.append({"name": n, "reason": "Uses " + ", ".join(fn["side_effects"]) + " (side effects). Auto-generated inputs are skipped; requirement examples still run."})
            continue
        if iteration == 1:
            fc, notes = _function_cases(fn, set(categories) | {"normal"}, cap, stress)
            warns += notes
        else:
            fc = _extra_cases(fn, max(4, cap // 2))
        cases += fc
    for f in analysis["functions"]:
        if f["is_async"]:
            skipped.append({"name": f["qualname"], "reason": "Async functions are not auto-tested."})
    for cls in analysis["classes"]:
        if tset and cls["name"] not in class_targets and not any(m.startswith(cls["name"] + ".") for m in method_targets):
            continue
        ctor, why, notes = _class_plan(analysis, cls)
        if ctor is None:
            skipped.append({"name": cls["name"], "reason": f"Class not auto-tested: {why}."})
            continue
        warns += notes
        for m in [methods[q] for q in methods if q.startswith(cls["name"] + ".")]:
            q = m["qualname"]
            deco = " ".join(m["decorators"])
            if m["name"].startswith("_") or m["is_async"] or "property" in deco or ".setter" in deco or "abstractmethod" in deco:
                continue
            if method_targets and q not in method_targets and cls["name"] not in class_targets:
                continue
            if extra_for is not None and q not in extra_for:
                continue
            if m["unsafe_to_autotest"]:
                skipped.append({"name": q, "reason": "Uses " + ", ".join(m["side_effects"]) + " (side effects)."})
                continue
            mm = {**m, "name": q}
            if iteration == 1:
                fc, notes = _function_cases(mm, set(categories) | {"normal"}, cap, stress)
                warns += notes
            else:
                fc = _extra_cases(mm, max(4, cap // 2))
            kept = []
            for c in fc:
                if c["expect"]["type"] in ("deterministic", "returns_type"):
                    continue  # a method may change the object or legitimately reject a fresh/empty object
                if c["expect"]["type"] == "no_exception":
                    c["expect"] = {"type": "robust"}
                    c["rationale"] += " (Methods run on a fresh object, so a deliberate ValueError/TypeError is also acceptable.)"
                c["ctor"] = ctor
                kept.append(c)
            cases += kept
    seen = {case_key(c) for c in existing}
    unique = []
    for c in cases:
        if case_key(c) not in seen:
            seen.add(case_key(c))
            unique.append(c)
    return unique, warns, skipped


def assign_ids(cases: list[dict], existing: list[dict], iteration: int) -> list[dict]:
    n = max([int(c["id"].split("-")[1]) for c in existing if c.get("id", "").startswith("TC-")] + [0])
    for c in cases:
        n += 1
        c["id"] = f"TC-{n:03d}"
        c["iteration"] = iteration
    return cases


def _compact(source, names):
    from tools.fixer import compact_context
    return compact_context(source, names)


def llm_cases(llm, source: str, analysis: dict, requirements: str, objective: str, targets: list[str], limit: int = 15):
    """Ask the LLM for semantic test cases. Invalid items are dropped; expectations are marked unverified."""
    top = {f["name"]: f for f in analysis["functions"] if not f["is_method"] and not f["is_async"]}
    names = [n for n in (targets or top) if n in top]
    sigs = "\n".join(f"- {n}({', '.join(p['name'] + (': ' + p['annotation'] if p['annotation'] else '') for p in top[n]['params'])})"
                     for n in names)
    system = ("You are a meticulous QA engineer. Reply with ONLY a JSON array, no prose, no markdown fences. Each item: "
              '{"function": str, "name": str, "category": "normal|boundary|edge|negative", "args": "Python tuple literal e.g. (2, 3)", '
              '"kwargs": "Python dict literal e.g. {}", "expect": "== <literal> | raises <ExceptionName> | no exception | robust", "rationale": str}. '
              "Use only Python literals in args/kwargs/expect. Use '== value' ONLY when the requirements or docstring determine the exact output; "
              "otherwise use 'no exception' or 'robust'. Never invent expected values you cannot justify.")
    user = (f"Objective: {objective}\nRequirements:\n{requirements or '(none given)'}\n\nFunctions to test:\n{sigs}\n\n"
            f"Source:\n{_compact(source, names)}\n\nReturn at most {limit} test cases.")
    raw = llm.complete(system, user, max_tokens=3000)
    data = llm.extract_json(raw)
    out, warns = [], []
    if not isinstance(data, list):
        raise ValueError("LLM did not return a JSON array")
    for item in data[:limit]:
        try:
            c = {"function": item["function"], "name": f"{item['function']}: {str(item.get('name', 'AI case'))[:70]}",
                 "category": item.get("category") if item.get("category") in CATEGORIES else "edge",
                 "args": str(item.get("args") or "()"), "kwargs": str(item.get("kwargs") or "{}"),
                 "expect": parse_expect(str(item.get("expect", "no exception"))),
                 "rationale": str(item.get("rationale", "AI-proposed case."))[:300], "source": "llm",
                 "oracle": "llm_unverified" if str(item.get("expect", "")).strip().startswith(("==", "raises")) else "heuristic",
                 "enabled": True}
            errs = validate_case(c, set(top))
            if errs:
                raise ValueError("; ".join(errs))
            out.append(c)
        except Exception as e:  # noqa: BLE001
            warns.append(f"Dropped an invalid AI-generated case ({str(e)[:80]}).")
    return out, warns


# ------------------------------------------------------------ pytest export
def export_pytest(cases: list[dict], module_name: str = "submitted", module_exceptions: list[str] | None = None) -> str:
    extra_exc = "".join(f", m.{e}" for e in (module_exceptions or []))
    head = f'''"""pytest suite generated by TestPilot AI. Put this file next to {module_name}.py and run: pytest -v

Expectations marked `oracle=heuristic` only check "does not crash" style properties; they do not prove correctness.
"""
import math

import pytest

import {module_name} as m

inf = float("inf")
nan = float("nan")


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if isinstance(a, float) and math.isnan(a):
            return isinstance(b, float) and math.isnan(b)
        return a == b or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b

'''
    body = []
    builtin_exc = set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__)
    for c in cases:
        if not c.get("enabled", True) or validate_case(c):
            continue
        ident = re.sub(r"\W+", "_", c["id"].lower() + "_" + c["name"].lower()).strip("_")[:70]
        fname = c["function"]
        pre: list[str] = []
        if "." in fname:
            cls_, meth_ = fname.split(".", 1)
            obj = f"m.{cls_}({_call_text(c.get('ctor') or '()', '{}')})"
            if c.get("setup"):
                pre = [f"    obj = {obj}"] + [f"    obj.{s['m']}({_call_text(s['args'], s['kwargs'])})" for s in c["setup"]]
                obj = "obj"
            call = f"{obj}.{meth_}({_call_text(c.get('args'), c.get('kwargs'))})"
        else:
            call = f"m.{fname}({_call_text(c.get('args'), c.get('kwargs'))})"
        e = c["expect"]
        t = e["type"]
        lines = [f"def test_{ident}():", f'    """{c["category"]} | oracle={c.get("oracle")} | {c.get("rationale", "")[:110].replace(chr(34), chr(39))}"""'] + pre
        if t == "equals":
            lines.append(f"    assert _same({call}, {e['value']})")
        elif t == "raises":
            exc = e["exception"] if e["exception"] in builtin_exc else f"m.{e['exception']}"
            lines += [f"    with pytest.raises({exc}):", f"        {call}"]
        elif t == "no_exception":
            lines.append(f"    {call}  # must not raise")
        elif t == "returns_type":
            lines.append(f"    assert isinstance({call}, {e['name']})")
        elif t == "deterministic":
            lines.append(f"    assert _same({call}, {call})")
        elif t == "robust":
            lines += ["    try:", f"        {call}", f"    except (TypeError, ValueError, KeyError, NotImplementedError{extra_exc}):",
                      "        pass  # deliberate rejection of invalid input is acceptable"]
        body.append("\n".join(lines))
    return head + "\n\n\n".join(body) + "\n"
