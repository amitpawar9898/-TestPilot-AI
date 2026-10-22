"""Static analysis: AST inspection + optional Ruff. Source is parsed, never executed."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from functools import lru_cache

IO_MODULES = {"os", "subprocess", "socket", "requests", "urllib", "http", "shutil", "pathlib", "sqlite3",
              "threading", "multiprocessing", "ctypes", "smtplib", "ftplib", "tempfile", "httpx", "aiohttp"}
NONDET_MODULES = {"random", "time", "datetime", "uuid", "secrets"}
DANGEROUS_CALLS = {"input", "open", "exec", "eval", "__import__", "compile", "breakpoint", "exit", "quit"}
STR_METHODS = {"lower", "upper", "split", "strip", "lstrip", "rstrip", "replace", "startswith", "endswith",
               "isdigit", "isalnum", "isalpha", "format", "encode", "title", "capitalize", "find", "splitlines", "isspace"}
LIST_METHODS = {"append", "extend", "sort", "pop", "insert", "remove", "reverse"}
DICT_METHODS = {"items", "keys", "values", "get", "update", "setdefault"}
SEVERITIES = ["Critical", "High", "Medium", "Low"]


def _un(node) -> str | None:
    return ast.unparse(node) if node is not None else None


def _params(args: ast.arguments) -> list[dict]:
    out = []
    pos = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
    for a, d in zip(pos, defaults):
        out.append({"name": a.arg, "annotation": _un(a.annotation), "has_default": d is not None,
                    "default": _un(d), "kind": "positional"})
    if args.vararg:
        out.append({"name": args.vararg.arg, "annotation": _un(args.vararg.annotation), "has_default": False,
                    "default": None, "kind": "vararg"})
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        out.append({"name": a.arg, "annotation": _un(a.annotation), "has_default": d is not None,
                    "default": _un(d), "kind": "keyword_only"})
    if args.kwarg:
        out.append({"name": args.kwarg.arg, "annotation": _un(args.kwarg.annotation), "has_default": False,
                    "default": None, "kind": "kwarg"})
    return out


def _complexity(fn) -> int:
    c = 1
    for n in ast.walk(fn):
        if isinstance(n, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp, ast.comprehension, ast.Assert)):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif hasattr(ast, "match_case") and isinstance(n, ast.match_case):
            c += 1
    return c


def _param_hints(fn, names: set[str]) -> dict[str, list[str]]:
    hints: dict[str, set[str]] = {n: set() for n in names}

    def base(x):
        return x.id if isinstance(x, ast.Name) and x.id in names else None

    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.args:
                b = base(n.args[0])
                if n.func.id == "len" and b:
                    hints[b].add("sized")
                if n.func.id in ("int", "float") and b:
                    hints[b].add("convertible")
                if n.func.id == "range":
                    for a in n.args:
                        if base(a):
                            hints[base(a)].add("int")
                if n.func.id in ("sum", "sorted", "max", "min", "list", "set", "enumerate", "reversed") and b:
                    hints[b].add("iterable")
            if isinstance(n.func, ast.Attribute) and base(n.func.value):
                b, attr = base(n.func.value), n.func.attr
                if attr in STR_METHODS:
                    hints[b].add("str")
                elif attr in LIST_METHODS:
                    hints[b].add("list")
                elif attr in DICT_METHODS:
                    hints[b].add("dict")
        elif isinstance(n, ast.comprehension) and base(n.iter):
            hints[base(n.iter)].add("iterable")
        elif isinstance(n, ast.For) and base(n.iter):
            hints[base(n.iter)].add("iterable")
        elif isinstance(n, ast.Subscript) and base(n.value):
            hints[base(n.value)].add("indexable")
        elif isinstance(n, (ast.BinOp, ast.Compare)):
            parts = [n.left, n.right] if isinstance(n, ast.BinOp) else [n.left, *n.comparators]
            for p in parts:
                if base(p):
                    for q in parts:
                        if isinstance(q, ast.Constant):
                            if isinstance(q.value, str):
                                hints[base(p)].add("str")
                            elif isinstance(q.value, float):
                                hints[base(p)].add("float")
                            elif isinstance(q.value, int) and not isinstance(q.value, bool):
                                hints[base(p)].add("number")
    return {k: sorted(v) for k, v in hints.items()}


def _impurity(fn, module_imports: dict[str, str]) -> dict:
    io_reasons, nondet = set(), set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            mod = module_imports.get(n.id, n.id)
            if mod in IO_MODULES:
                io_reasons.add(mod)
            if mod in NONDET_MODULES:
                nondet.add(mod)
            if isinstance(n.ctx, ast.Load) and n.id in DANGEROUS_CALLS and n.id not in ("compile",):
                io_reasons.add(n.id)
    return {"unsafe_to_autotest": bool(io_reasons), "side_effects": sorted(io_reasons), "nondeterministic": sorted(nondet)}


def _guard_names(fn) -> set[str]:
    names: set[str] = set()
    tests = []
    for n in ast.walk(fn):
        if isinstance(n, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            tests.append(n.test)
        elif isinstance(n, (ast.Compare, ast.BoolOp)):
            tests.append(n)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            tests.append(n)
    for t in tests:
        names |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
    return names


def _finding(rule, title, desc, sev, line, fix, fn=None, tool="ast", filename="submitted.py"):
    return {"category": "static", "rule": rule, "title": title, "description": desc, "severity": sev, "line": line,
            "file": filename, "function": fn, "suggested_fix": fix, "source_tool": tool,
            "validation_status": "Static analysis (not executed)"}


def _ast_checks(tree, funcs, filename, ruff_ran) -> list[dict]:
    out = []
    add = lambda *a, **k: out.append(_finding(*a, filename=filename, **k))  # noqa: E731
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x is not None]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set")):
                    add("TP001", f"Mutable default argument in {node.name}()",
                        "A mutable default is created once and shared between calls, so state leaks across calls.",
                        "Medium", d.lineno, "Use `None` as the default and create the list/dict inside the function.", fn=node.name)
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add("TP002", "Bare `except:` clause", "Catches every exception including KeyboardInterrupt and hides real errors.",
                    "Medium", node.lineno, "Catch specific exception types.")
            elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                add("TP003", "Exception silently swallowed", "An exception handler that only does `pass` hides failures.",
                    "Low", node.lineno, "Log or re-raise the exception, or handle it explicitly.")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            add("TP004", f"Use of {node.func.id}()", "Executing dynamic code can run attacker-controlled input.",
                "High", node.lineno, "Use ast.literal_eval or a dedicated parser instead.")
        elif isinstance(node, ast.Compare) and not ruff_ran:
            for op, c in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None:
                    add("TP005", "Comparison to None with ==/!=", "Use identity checks for None.", "Low", node.lineno,
                        "Replace `== None` with `is None` (and `!= None` with `is not None`).")
    if not ruff_ran:
        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        imported[a.asname or a.name] = node.lineno
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for name, line in imported.items():
            if name not in used:
                add("TP007", f"Unused import '{name}'", "The import is never referenced.", "Low", line, "Remove the import.")
    by_name = {f["qualname"]: f for f in funcs}
    for fn_node in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        meta = next((f for f in funcs if f["lineno"] == fn_node.lineno and f["name"] == fn_node.name), None)
        if not meta:
            continue
        pnames = {p["name"] for p in meta["params"]}
        guards = _guard_names(fn_node)
        flagged = set()
        for b in ast.walk(fn_node):
            if isinstance(b, ast.BinOp) and isinstance(b.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                r = b.right
                nm = r.id if isinstance(r, ast.Name) else (
                    r.args[0].id if isinstance(r, ast.Call) and isinstance(r.func, ast.Name) and r.func.id == "len"
                    and r.args and isinstance(r.args[0], ast.Name) else None)
                if nm in pnames and nm not in guards and nm not in flagged:
                    flagged.add(nm)
                    add("TP006", f"Possible division by zero in {meta['name']}()",
                        f"Divides by a value derived from parameter '{nm}' with no visible zero/empty check (unverified heuristic).",
                        "Low", b.lineno, f"Validate '{nm}' before dividing and raise a clear ValueError if it is zero or empty.", fn=meta["name"])
        if meta["complexity"] > 10:
            add("TP008", f"High cyclomatic complexity in {meta['name']}() ({meta['complexity']})",
                "Many branches make the function hard to test and reason about.", "Medium" if meta["complexity"] > 15 else "Low",
                meta["lineno"], "Split the function into smaller units.", fn=meta["name"])
        length = (meta["end_lineno"] or meta["lineno"]) - meta["lineno"] + 1
        if length > 60:
            add("TP009", f"Long function {meta['name']}() ({length} lines)", "Long functions hide bugs.", "Low",
                meta["lineno"], "Extract helper functions.", fn=meta["name"])
        if not meta["docstring"] and not meta["name"].startswith("_") and not meta["is_method"]:
            add("TP010", f"Missing docstring in {meta['name']}()", "Without documented behavior, tests cannot check intent.",
                "Low", meta["lineno"], "Add a docstring with the contract and examples (`>>> f(1)` doctests become tests).", fn=meta["name"])
    return out


@lru_cache(maxsize=1)
def ruff_command() -> tuple[str, ...] | None:
    exe = shutil.which("ruff")
    if exe:
        return (exe,)
    try:
        r = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return (sys.executable, "-m", "ruff")
    except Exception:  # noqa: BLE001
        pass
    return None


def _ruff_severity(code: str) -> str:
    if code.startswith("E9") or code in ("F821", "F822", "F823", "F811") or code.startswith("S10") and code in ("S102", "S307"):
        return "High"
    if code.startswith(("F", "S", "B")):
        return "Medium"
    return "Low"


def run_ruff(source: str, filename: str) -> dict:
    cmd = ruff_command()
    if not cmd:
        return {"available": False, "ran": False, "findings": [], "error": "Ruff is not installed (pip install ruff)."}
    try:
        r = subprocess.run([*cmd, "check", "-", "--stdin-filename", "submitted.py", "--output-format", "json", "--isolated",
                            "--no-cache", "--select", "E,F,W,B,S,C90", "--ignore", "S101,E501"],
                           input=source, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or "[]")
    except Exception as e:  # noqa: BLE001
        return {"available": True, "ran": False, "findings": [], "error": f"Ruff failed: {type(e).__name__}: {e}"}
    out = []
    for d in data:
        code = d.get("code") or "RUF"
        msg = d.get("message", "")
        loc = d.get("location") or {}
        out.append(_finding(f"ruff:{code}", f"{code}: {msg}", f"Ruff rule {code}. {msg}", _ruff_severity(code),
                            loc.get("row"), "See " + (d.get("url") or "the Ruff rule documentation") + ".", tool="ruff", filename=filename))
    return {"available": True, "ran": True, "findings": out, "error": None}


def analyze_source(source: str, filename: str = "submitted.py", use_ruff: bool = True) -> dict:
    lines = source.splitlines()
    metrics = {"loc": len(lines), "sloc": sum(1 for l in lines if l.strip() and not l.strip().startswith("#")),
               "comment_lines": sum(1 for l in lines if l.strip().startswith("#"))}
    meta = {"filename": filename, "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "line_count": len(lines), "byte_count": len(source.encode("utf-8"))}
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        text = (e.text or "").rstrip("\n")
        err = {"message": e.msg, "line": e.lineno, "column": e.offset, "text": text}
        f = _finding("E999", f"Syntax error: {e.msg}", f"The file cannot be parsed (line {e.lineno}). No tests can be generated or run.",
                     "Critical", e.lineno, "Fix the syntax error and re-run the analysis.", filename=filename)
        f["source_tool"] = "python-ast"
        meta.update(function_count=0, class_count=0)
        return {"ok": False, "syntax_error": err, "functions": [], "classes": [], "imports": [], "dependencies": [],
                "third_party": [], "metrics": {**metrics, "functions": 0, "classes": 0, "avg_complexity": 0, "max_complexity": 0},
                "findings": [f], "ruff": {"available": ruff_command() is not None, "ran": False, "error": None},
                "meta": meta, "module_exceptions": []}
    module_imports: dict[str, str] = {}
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                module_imports[(a.asname or a.name).split(".")[0]] = a.name.split(".")[0]
                imports.append({"module": a.name, "names": [], "lineno": n.lineno})
        elif isinstance(n, ast.ImportFrom):
            top = (n.module or "").split(".")[0]
            for a in n.names:
                module_imports[a.asname or a.name] = top
            imports.append({"module": n.module or ".", "names": [a.name for a in n.names], "lineno": n.lineno})
    deps = sorted({(i["module"] or ".").split(".")[0] for i in imports if i["module"] != "."})
    stdlib = getattr(sys, "stdlib_module_names", set())
    third_party = [d for d in deps if d not in stdlib and d != "__future__"]
    funcs, classes = [], []
    exc_names: list[str] = []

    def add_func(node, cls=None):
        params = _params(node.args)
        names = {p["name"] for p in params}
        raises = sorted({(r.exc.func.id if isinstance(r.exc, ast.Call) and isinstance(r.exc.func, ast.Name) else
                          r.exc.id if isinstance(r.exc, ast.Name) else "") for r in ast.walk(node)
                         if isinstance(r, ast.Raise) and r.exc is not None} - {""})
        funcs.append({
            "name": node.name, "qualname": f"{cls}.{node.name}" if cls else node.name, "class_name": cls,
            "is_method": cls is not None, "is_async": isinstance(node, ast.AsyncFunctionDef),
            "lineno": node.lineno, "end_lineno": getattr(node, "end_lineno", node.lineno), "params": params,
            "returns": _un(node.returns), "docstring": ast.get_docstring(node) or "", "complexity": _complexity(node),
            "decorators": [_un(d) for d in node.decorator_list], "raises": raises,
            "param_hints": _param_hints(node, names), **_impurity(node, module_imports)})

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_func(node)
        elif isinstance(node, ast.ClassDef):
            methods = [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for m in methods:
                add_func(m, node.name)
            bases = [_un(b) for b in node.bases]
            classes.append({"name": node.name, "lineno": node.lineno, "bases": bases, "methods": [m.name for m in methods]})
            if any(b and (b.endswith("Exception") or b.endswith("Error")) for b in bases):
                exc_names.append(node.name)
    module_body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    script_mode = not funcs and bool(module_body)
    ruff = run_ruff(source, filename) if use_ruff else {"available": ruff_command() is not None, "ran": False, "findings": [], "error": "Ruff disabled."}
    findings = _ast_checks(tree, funcs, filename, ruff["ran"]) + ruff["findings"]
    cx = [f["complexity"] for f in funcs] or [0]
    metrics.update(functions=len(funcs), classes=len(classes), avg_complexity=round(sum(cx) / len(cx), 2), max_complexity=max(cx))
    meta.update(function_count=len(funcs), class_count=len(classes))
    return {"ok": True, "syntax_error": None, "functions": funcs, "classes": classes, "imports": imports,
            "dependencies": deps, "third_party": third_party, "script_mode": script_mode, "metrics": metrics, "findings": findings,
            "ruff": {k: ruff[k] for k in ("available", "ran", "error")}, "meta": meta, "module_exceptions": exc_names}
