"""Conservative quality edits and measurable source metrics for ImproveAgent."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.code_analyzer import IO_MODULES
from tools.fixer import make_diff
from tools.fixer import compact_context, parses, replace_function, validate_function_source
from tools.llm_client import LLMClient, detect_ollama

SAFE_RULES = ("F401", "F541", "UP032")  # Verified with Ruff 0.17.1; unsafe F841 remains excluded.


def _ruff(args: list[str], path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "ruff", *args, str(path)], capture_output=True, text=True, timeout=20)


def _public_api(source: str) -> dict[str, tuple[str, str]]:
    tree = ast.parse(source)
    found: dict[str, tuple[str, str]] = {}

    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                if not node.name.startswith("_") or prefix:
                    if isinstance(node, ast.ClassDef):
                        found[name] = ("class", "")
                        walk(node.body, name)
                    else:
                        returns = ast.dump(node.returns, include_attributes=False) if node.returns else "None"
                        found[name] = ("function", ast.dump(node.args, include_attributes=False) + "->" + returns)
    walk(tree.body)
    return found


def _metrics(source: str, filename: str) -> dict:
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    public = [n for n in functions if not n.name.startswith("_")]
    def complexity(fn):
        score = 1
        for node in ast.walk(fn):
            if isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp, ast.comprehension, ast.Assert)):
                score += 1
            elif isinstance(node, ast.BoolOp):
                score += len(node.values) - 1
        return score
    complexities = [complexity(f) for f in functions]
    docstrings = sum(ast.get_docstring(n) is not None for n in public)
    typed = sum(bool(n.returns) and all(a.annotation is not None for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs)) for n in public)
    with tempfile.TemporaryDirectory(prefix="testpilot_metrics_") as td:
        path = Path(td) / Path(filename).name
        path.write_text(source, encoding="utf-8")
        check = _ruff(["check", "--output-format", "json"], path)
        try:
            issues = len(json.loads(check.stdout)) if check.stdout.strip() else 0
        except ValueError:
            issues = 0
        formatted = _ruff(["format", "--check"], path)
    return {"ruff_issues": issues, "needs_formatting": int(formatted.returncode != 0),
            "function_count": len(functions), "public_function_count": len(public),
            "docstring_coverage_percent": round(100 * docstrings / len(public), 1) if public else 100.0,
            "type_hint_coverage_percent": round(100 * typed / len(public), 1) if public else 100.0,
            "cyclomatic_complexity_total": sum(complexities), "lines": len(source.splitlines())}


def improve_source(source: str, filename: str) -> dict:
    before_metrics = _metrics(source, filename)
    current = source
    applied: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="testpilot_improve_") as td:
        path = Path(td) / Path(filename).name
        path.write_text(current, encoding="utf-8")
        rules = tuple(rule for rule in SAFE_RULES if rule != "F401") if path.name == "__init__.py" else SAFE_RULES
        descriptions = {"F401": "Removed an unused import.", "F541": "Removed a redundant f-string prefix.",
                        "UP032": "Converted a simple str.format call to an equivalent f-string."}
        for rule in rules:
            lint = _ruff(["check", "--fix", "--select", rule], path)
            if lint.returncode not in (0, 1):
                return {"source": source, "applied": [], "reason": "Ruff safe checks could not run: " + (lint.stderr.strip()[:200] or "unknown error")}
            changed = path.read_text(encoding="utf-8")
            if changed != current:
                applied.append({"rule": rule, "description": descriptions[rule]})
                current = changed
        path.write_text(current, encoding="utf-8")
        formatted = _ruff(["format"], path)
        if formatted.returncode != 0:
            return {"source": source, "applied": [], "reason": "Ruff formatter failed: " + formatted.stderr.strip()[:200]}
        changed = path.read_text(encoding="utf-8")
        if changed != current:
            applied.append({"rule": "Ruff format", "description": "Applied standard Python formatting."})
            current = changed

    if current == source:
        return {"source": source, "applied": [], "before_metrics": before_metrics, "after_metrics": before_metrics,
                "reason": "No safe improvements found: Ruff found no applicable safe edits."}
    try:
        api_same = _public_api(source) == _public_api(current)
    except SyntaxError:
        api_same = False
    if not api_same:
        return {"source": source, "applied": [], "reason": "Candidate rejected: public function and class signatures changed."}
    original_tree, candidate_tree = ast.parse(source), ast.parse(current)
    def imported_modules(tree):
        return {module for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for module in ([item.name.split(".")[0] for item in node.names] if isinstance(node, ast.Import)
                               else [(node.module or "").split(".")[0]])}
    original_imports, candidate_imports = imported_modules(original_tree), imported_modules(candidate_tree)
    added_io = (candidate_imports - original_imports) & IO_MODULES
    def dangerous_counts(tree):
        return {name: sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name for n in ast.walk(tree))
                for name in ("eval", "exec", "open")}
    old_calls, new_calls = dangerous_counts(original_tree), dangerous_counts(candidate_tree)
    added_danger = {name for name, count in new_calls.items() if count > old_calls[name]}
    if added_io or added_danger:
        return {"source": source, "applied": [], "reason": "Candidate rejected: it introduced a prohibited I/O import or dynamic execution call."}
    after_metrics = _metrics(current, filename)
    better = (after_metrics["ruff_issues"] < before_metrics["ruff_issues"]
              or after_metrics["needs_formatting"] < before_metrics["needs_formatting"])
    if not better:
        return {"source": source, "applied": [], "before_metrics": before_metrics, "after_metrics": after_metrics,
                "reason": "No safe improvements found: no measured Ruff or formatting metric improved."}
    return {"source": current, "applied": applied, "before_metrics": before_metrics, "after_metrics": after_metrics,
            "diff": make_diff(source, current, filename), "reason": "Safe lint/format edits improved measured code-quality metrics."}


def ollama_improve_source(source: str, filename: str, base_url: str = "http://localhost:11434/v1") -> dict:
    """Optional local-only simplification; targets complete functions and validates each replacement."""
    available, models = detect_ollama(base_url)
    if not available or not models:
        return {"source": source, "applied": [], "reason": "Local Ollama is not available."}
    model = models[0]
    llm = LLMClient("ollama", model, base_url)
    tree = ast.parse(source)
    targets = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
    if not targets:
        return {"source": source, "applied": [], "reason": "No public top-level functions are available for local simplification."}
    system = ("Improve readability and simplify Python function bodies while preserving exact behavior. Return only a JSON array of objects "
              '{"function": name, "new_source": complete_function}. Do not change names, signatures, annotations, return types, or imports. '
              "Do not add I/O, open, eval, or exec. If no safe simplification is clear, return [].")
    user = "Refactor only these functions using the provided source context.\n\n" + compact_context(source, targets)
    try:
        proposals = llm.extract_json(llm.complete(system, user, max_tokens=3000))
    except Exception as exc:  # noqa: BLE001
        return {"source": source, "applied": [], "reason": f"Local Ollama improvement failed: {type(exc).__name__}."}
    if not isinstance(proposals, list):
        return {"source": source, "applied": [], "reason": "Local Ollama returned no valid proposal list."}
    current, applied = source, []
    for item in proposals[:min(5, 5)]:
        if not isinstance(item, dict) or item.get("function") not in targets or not isinstance(item.get("new_source"), str):
            continue
        error = validate_function_source(item["new_source"], item["function"])
        candidate = None if error else replace_function(current, item["function"], item["new_source"])
        if not error and candidate and candidate != current and parses(candidate) and _public_api(current) == _public_api(candidate):
            current = candidate
            applied.append({"rule": "Ollama local", "description": f"Simplified {item['function']}() using the local model {model}."})
    if not applied:
        return {"source": source, "applied": [], "reason": "Ollama found no safe improvement."}
    before, after = _metrics(source, filename), _metrics(current, filename)
    better = after["ruff_issues"] < before["ruff_issues"] or after["needs_formatting"] < before["needs_formatting"] or after["cyclomatic_complexity_total"] < before["cyclomatic_complexity_total"]
    if not better:
        return {"source": source, "applied": [], "reason": "Ollama proposal rejected: no measured code-quality metric improved."}
    return {"source": current, "applied": applied, "before_metrics": before, "after_metrics": after,
            "diff": make_diff(source, current, filename), "reason": "Local Ollama simplification improved measured code-quality metrics."}
