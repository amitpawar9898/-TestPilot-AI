"""Standalone test harness. It runs INSIDE the isolated environment (Docker container, or a local process in
trusted mode) and is also imported by the host for `safe_literal`. It never uses eval(): case values are parsed
by a small, bounded literal evaluator."""
import ast
import contextlib
import importlib.util
import io
import json
import math
import os
import sys
import time
import traceback

MARK = "@@TP@@"
MAX_SEQ = 5_000_000
_REAL_OUT = sys.__stdout__
_NAMES = {"None": None, "True": True, "False": False, "inf": float("inf"), "nan": float("nan")}
_FLOATS = {"inf", "-inf", "+inf", "nan", "infinity", "-infinity"}


class HarnessTimeout(BaseException):
    """BaseException so that `except Exception` in the code under test cannot swallow it."""


def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _ev(n):
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id in _NAMES:
            return _NAMES[n.id]
        raise ValueError(f"name '{n.id}' is not allowed")
    if isinstance(n, ast.Tuple):
        return tuple(_ev(e) for e in n.elts)
    if isinstance(n, ast.List):
        return [_ev(e) for e in n.elts]
    if isinstance(n, ast.Set):
        return {_ev(e) for e in n.elts}
    if isinstance(n, ast.Dict):
        if any(k is None for k in n.keys):
            raise ValueError("dict unpacking is not allowed")
        return {_ev(k): _ev(v) for k, v in zip(n.keys, n.values)}
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        v = _ev(n.operand)
        if not _isnum(v):
            raise ValueError("unary operator needs a number")
        return -v if isinstance(n.op, ast.USub) else +v
    if isinstance(n, ast.BinOp):
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Mult):
            for x, y in ((a, b), (b, a)):
                if isinstance(x, (str, bytes, list, tuple)) and isinstance(y, int) and not isinstance(y, bool):
                    if len(x) * max(y, 0) > MAX_SEQ:
                        raise ValueError("value too large")
                    return x * y
            if _isnum(a) and _isnum(b):
                return a * b
        elif isinstance(n.op, ast.Add):
            if _isnum(a) and _isnum(b):
                return a + b
            if type(a) is type(b) and isinstance(a, (str, list, tuple)):
                return a + b
        elif isinstance(n.op, ast.Sub) and _isnum(a) and _isnum(b):
            return a - b
        elif isinstance(n.op, ast.Pow) and _isnum(a) and isinstance(b, int) and 0 <= b <= 64 and abs(a) <= 10**6:
            return a ** b
        raise ValueError("expression not allowed")
    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float" and len(n.args) == 1
            and not n.keywords and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
            and n.args[0].value.strip().lower() in _FLOATS):
        return float(n.args[0].value)
    raise ValueError("unsupported expression")


def safe_literal(text):
    return _ev(ast.parse(str(text).strip(), mode="eval").body)


def parse_args(text):
    v = safe_literal(text or "()")
    return v if isinstance(v, tuple) else (v,)


def parse_kwargs(text):
    v = safe_literal(text or "{}")
    if not isinstance(v, dict) or not all(isinstance(k, str) for k in v):
        raise ValueError("kwargs must be a dict with string keys")
    return v


def _same(a, b):
    if _isnum(a) and _isnum(b):
        if isinstance(a, float) and math.isnan(a):
            return isinstance(b, float) and math.isnan(b)
        return a == b or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    try:
        return bool(a == b)
    except Exception:
        return False


def _rep(v, limit=300):
    try:
        s = repr(v)
    except Exception as e:  # noqa: BLE001
        s = f"<unrepresentable {type(v).__name__}: {type(e).__name__}>"
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


class _Cap(io.TextIOBase):
    def __init__(self, limit=4000):
        self.parts, self.n, self.limit = [], 0, limit

    def write(self, s):
        if self.n < self.limit:
            self.parts.append(s[: self.limit - self.n])
        self.n += len(s)
        return len(s)

    def text(self):
        t = "".join(self.parts)
        return t + "...[truncated]" if self.n > self.limit else t


def _emit(obj):
    _REAL_OUT.write(MARK + json.dumps(obj) + "\n")
    _REAL_OUT.flush()


def _arm(seconds):
    try:
        import signal
        if hasattr(signal, "setitimer"):
            def handler(signum, frame):
                raise HarnessTimeout()
            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, seconds)
    except Exception:  # noqa: BLE001
        pass


def _disarm():
    try:
        import signal
        if hasattr(signal, "setitimer"):
            signal.setitimer(signal.ITIMER_REAL, 0)
    except Exception:  # noqa: BLE001
        pass


def _exc_info(e):
    tb = e.__traceback__.tb_next if e.__traceback__ else None
    text = "".join(traceback.format_exception(type(e), e, tb))
    line = None
    for fr in reversed(traceback.extract_tb(e.__traceback__)):
        if os.path.basename(fr.filename) == "user_module.py":
            line = fr.lineno
            break
    return {"exception_type": type(e).__name__, "exception_message": str(e)[:300], "traceback": text[-1200:], "line": line}


def _evaluate(func, args, kwargs, expect, mod):
    t = expect.get("type")
    if t in ("equals", "returns_type", "no_exception", "deterministic"):
        try:
            val = func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": f"Unexpected {type(e).__name__}: {e}"[:300], **_exc_info(e)}
        if t == "no_exception":
            return {"status": "passed", "actual": _rep(val), "detail": "Completed without raising."}
        if t == "equals":
            exp = safe_literal(expect["value"])
            ok = _same(val, exp)
            return {"status": "passed" if ok else "failed", "actual": _rep(val),
                    "detail": "Output matches expected." if ok else f"Expected {_rep(exp, 120)} but got {_rep(val, 120)}."}
        if t == "returns_type":
            want = {"int": int, "float": float, "str": str, "bool": bool, "list": list, "dict": dict,
                    "tuple": tuple, "set": set}.get(expect.get("name"))
            ok = want is not None and isinstance(val, want)
            return {"status": "passed" if ok else "failed", "actual": _rep(val),
                    "detail": f"Returned {type(val).__name__}, expected {expect.get('name')}." if not ok else "Return type matches."}
        try:
            val2 = func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": f"Second call raised {type(e).__name__}: {e}"[:300], **_exc_info(e)}
        ok = _same(val, val2)
        return {"status": "passed" if ok else "failed", "actual": _rep(val),
                "detail": "Same result on repeated call." if ok else f"Repeated call differed: {_rep(val, 80)} vs {_rep(val2, 80)}."}
    if t == "raises":
        want = expect.get("exception", "Exception")
        try:
            val = func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            names = [c.__name__ for c in type(e).__mro__]
            ok = want in names
            return {"status": "passed" if ok else "failed", "actual": f"raised {type(e).__name__}",
                    "detail": f"Raised {type(e).__name__} as expected." if ok else f"Expected {want} but raised {type(e).__name__}: {e}"[:300],
                    **_exc_info(e)}
        return {"status": "failed", "actual": _rep(val), "detail": f"Expected {want} to be raised, but the call returned {_rep(val, 100)}."}
    if t == "robust":
        try:
            val = func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            deliberate = isinstance(e, (TypeError, ValueError, KeyError, NotImplementedError)) or type(e).__module__ == mod.__name__
            if deliberate:
                return {"status": "passed", "actual": f"raised {type(e).__name__}", "detail": "Invalid input was rejected with a clear exception."}
            return {"status": "failed", "actual": f"raised {type(e).__name__}",
                    "detail": f"Invalid input caused an unhandled {type(e).__name__}: {e}"[:300], **_exc_info(e)}
        return {"status": "passed", "actual": _rep(val), "detail": "Invalid input handled without crashing."}
    return {"status": "error", "detail": f"Unknown expectation type: {t}"}


def _resolve(mod, case):
    """Top-level function, or `Class.method` called on a fresh instance built from case['ctor']."""
    name = case["function"]
    if name == "__module__":
        return lambda: None  # The module was already executed once during isolated import.
    if "." in name:
        cls_name, meth = name.split(".", 1)
        cls = getattr(mod, cls_name, None)
        if not isinstance(cls, type):
            raise LookupError(f"class '{cls_name}' not found in module")
        obj = cls(*parse_args(case.get("ctor") or "()"))
        for step in case.get("setup") or []:  # state-building calls made before the call under test
            getattr(obj, step["m"])(*parse_args(step.get("args")), **parse_kwargs(step.get("kwargs")))
        func = getattr(obj, meth, None)
    else:
        func = getattr(mod, name, None)
    if not callable(func):
        raise LookupError(f"function '{name}' not found in module")
    return func


def _run_case(mod, case, per_test):
    res = {"id": case["id"], "status": "error", "actual": "", "detail": "", "exception_type": "",
           "exception_message": "", "traceback": "", "stdout": "", "line": None}
    t0 = time.perf_counter()
    try:
        args, kwargs = parse_args(case.get("args")), parse_kwargs(case.get("kwargs"))
        parse_args(case.get("ctor") or "()")
    except Exception as e:  # noqa: BLE001
        res["detail"] = f"Invalid test definition: {e}"
        res["duration_ms"] = 0.0
        return res
    cap = _Cap()
    _arm(per_test)
    try:
        with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
            try:
                func = _resolve(mod, case)
            except LookupError as e:
                res.update(status="error", detail=f"Invalid test definition: {e}")
                func = None
            except Exception as e:  # noqa: BLE001 - constructor failed
                res.update(status="error", detail=f"Could not construct or set up the object: {type(e).__name__}: {e}"[:300], **_exc_info(e))
                func = None
            if func is not None:
                res.update(_evaluate(func, args, kwargs, case["expect"], mod))
    except HarnessTimeout:
        res.update(status="timeout", detail=f"Exceeded the {per_test}s per-test limit.")
    except BaseException as e:  # noqa: BLE001 - SystemExit etc. from code under test
        res.update(status="error", detail=f"{type(e).__name__} escaped the code under test.", **_exc_info(e))
    finally:
        _disarm()
    res["stdout"] = cap.text()
    res["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return res


def _limits(mem_mb):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if mem_mb > 0:
            b = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
    except Exception:  # noqa: BLE001
        pass


def main(argv):
    module_path, cases_path, per_test, mem_mb = argv[1], argv[2], float(argv[3]), int(argv[4])
    _limits(mem_mb)
    with open(cases_path, encoding="utf-8") as fh:
        cases = json.load(fh)
    mod, err = None, None
    _arm(per_test * 3)
    try:
        spec = importlib.util.spec_from_file_location("user_module", module_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["user_module"] = mod
        cap = _Cap()
        with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
            spec.loader.exec_module(mod)
    except HarnessTimeout:
        err = "Module import exceeded the time limit (top-level code too slow or looping)."
    except BaseException as e:  # noqa: BLE001
        err = f"Module failed to import: {type(e).__name__}: {e}"
    finally:
        _disarm()
    for c in cases:
        _emit({"type": "start", "id": c["id"]})
        if err:
            _emit({"type": "result", "id": c["id"], "status": "error", "detail": err, "actual": "", "exception_type": "ImportError",
                   "exception_message": err[:300], "traceback": "", "stdout": "", "line": None, "duration_ms": 0.0})
        else:
            _emit({"type": "result", **_run_case(mod, c, per_test)})
    _emit({"type": "done"})


if __name__ == "__main__":
    main(sys.argv)
