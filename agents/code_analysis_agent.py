from __future__ import annotations

import ast

from agents.base import AgentContext, AgentFailure, BaseAgent
from tools.code_analyzer import analyze_source
from tools.fixer import make_diff


def _repair_common_syntax(source: str) -> tuple[str, dict | None]:
    """Apply narrow syntax repairs, accepting them only if the complete module parses."""
    import re
    candidate = source
    changes = []
    for _ in range(5):
        syntax_error = None
        try:
            ast.parse(candidate)
            if not changes:
                return source, None
            descriptions = " ".join(change["description"] for change in changes)
            return candidate, {"line": changes[0]["line"], "description": descriptions, "changes": changes}
        except SyntaxError as caught:
            syntax_error = caught
            if not caught.lineno:
                return source, None

        lines = candidate.splitlines(keepends=True)
        index = syntax_error.lineno - 1
        if index < 0 or index >= len(lines):
            return source, None
        old = lines[index]
        ending = "\r\n" if old.endswith("\r\n") else "\n" if old.endswith("\n") else ""
        body = old[:-len(ending)] if ending else old
        candidate_line = None
        description = ""

        # A common typo: `if value = expected:` instead of `==`.
        if "cannot assign to expression here" in syntax_error.msg and "Maybe you meant '=='" in str(syntax_error):
            match = re.match(r"^(\s*(?:if|elif|while)\b)(.*)$", body)
            if match:
                condition = match.group(2)
                operators = list(re.finditer(r"(?<![=!<>])=(?!=)", condition))
                if len(operators) == 1:
                    op = operators[0]
                    condition = condition[:op.start()] + "==" + condition[op.end():]
                    candidate_line = match.group(1) + condition
                    description = f"Changed '=' to '==' on line {syntax_error.lineno}."

        # Repair a missing block colon only when Python reports that exact error.
        if candidate_line is None and syntax_error.msg == "expected ':'" and "#" not in body:
            stripped = body.rstrip()
            if stripped and stripped.split(None, 1)[0] in {"if", "elif", "else", "for", "while", "try", "except", "finally", "with", "def", "class", "async"}:
                candidate_line = stripped + ":" + body[len(stripped):]
                description = f"Added a missing ':' on line {syntax_error.lineno}."

        if candidate_line is None:
            return source, None
        lines[index] = candidate_line + ending
        candidate = "".join(lines)
        changes.append({"line": syntax_error.lineno, "description": description})

    try:
        ast.parse(candidate)
    except SyntaxError:
        return source, None
    descriptions = " ".join(change["description"] for change in changes)
    return candidate, {"line": changes[0]["line"], "description": descriptions, "changes": changes}


def _repair_syntax_with_ai(source: str, syntax_error, llm) -> tuple[str, str] | None:
    """Ask an explicitly configured provider for a syntax-only repair; accept only parseable full source."""
    if llm is None or len(source) > 60_000:
        return None
    system = (
        "Repair Python syntax only. Preserve the program's behavior, names, comments, and formatting wherever possible. "
        "Return only a JSON object with exactly two string fields: source (the complete corrected Python file) and explanation "
        "(a short description of the syntax repair). Do not return markdown or omit any source lines."
    )
    user = (f"Python reported {type(syntax_error).__name__} at line {syntax_error.lineno}, "
            f"column {syntax_error.offset}: {syntax_error.msg}.\n\nComplete source:\n{source}")
    try:
        result = llm.extract_json(llm.complete(system, user, max_tokens=7000))
        candidate = result.get("source") if isinstance(result, dict) else None
        explanation = result.get("explanation", "AI syntax correction") if isinstance(result, dict) else "AI syntax correction"
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        ast.parse(candidate)
        if candidate == source:
            return None
        return candidate, str(explanation)[:300]
    except Exception:  # noqa: BLE001 - invalid model output must never block static analysis
        return None


class CodeAnalysisAgent(BaseAgent):
    key, name = "code_analysis", "Code Analysis"

    def run(self, ctx: AgentContext) -> None:
        s, req, rec = ctx.state, ctx.state.request, ctx.rec
        rec.task = "Parse code and collect structure and static findings"
        rec.input_summary = f"{req.filename}: {len(req.code.splitlines())} lines, {len(req.code.encode('utf-8'))} bytes"
        original = req.code
        repaired, repair = _repair_common_syntax(original)
        if repair is not None:
            req.code = repaired
            change = {"rule": "PY-SYNTAX-001", "line": repair["line"], "description": repair["description"]}
            s.fix = {"syntax_repair": {"original": original, "change": change}}
            ctx.warn(f"Applied a safe syntax repair to the analyzed copy: {repair['description']} Review the fixed source below.")
        elif req.auto_fix and ctx.llm is not None:
            try:
                syntax_error = None
                try:
                    ast.parse(original)
                except SyntaxError as caught:
                    syntax_error = caught
                if syntax_error is not None:
                    result = ctx.tool(
                        f"llm:{ctx.settings.provider}", "Repair Python syntax",
                        _repair_syntax_with_ai, original, syntax_error, ctx.llm,
                        describe=lambda candidate: "returned a parseable full source file" if candidate else "no safe syntax repair returned",
                    )
                    if result is not None:
                        req.code, explanation = result
                        change = {"rule": "AI-SYNTAX-001", "line": syntax_error.lineno, "description": explanation}
                        s.fix = {"syntax_repair": {"original": original, "change": change}}
                        ctx.warn(f"AI repaired Python syntax: {explanation}")
            except Exception as error:  # noqa: BLE001
                ctx.warn(f"AI syntax repair failed; original syntax error is reported: {type(error).__name__}.")
        a = ctx.tool("ast.parse + checks", "Static analysis", analyze_source, req.code, req.filename,
                     describe=lambda r: "syntax error" if not r["ok"] else f"{len(r['functions'])} functions, {len(r['classes'])} classes, {len(r['findings'])} findings")
        s.analysis = a
        if not a["ok"]:
            e = a["syntax_error"]
            rec.output_summary = f"Syntax error at line {e['line']}"
            raise AgentFailure(f"Syntax error at line {e['line']}, column {e['column']}: {e['message']}. Test generation and execution are blocked until it is fixed.")
        r = a["ruff"]
        ctx.tool("ruff", "Lint with Ruff", lambda: r, describe=lambda x: "ran" if x["ran"] else (x["error"] or "skipped"))
        if not r["ran"] and r["error"]:
            ctx.warn(r["error"])
        if a["third_party"]:
            ctx.warn(f"Imports third-party package(s) {', '.join(a['third_party'])}; they are not installed in the isolated runtime, so tests that need them will error.")
        sev = {}
        for f in a["findings"]:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        rec.output_summary = (f"{len(a['functions'])} functions, {len(a['classes'])} classes, avg complexity {a['metrics']['avg_complexity']}; "
                              f"findings: {', '.join(f'{v} {k}' for k, v in sev.items()) or 'none'}")
        rec.reason = "Code parsed successfully; structured findings carry severity and line numbers."
        if a.get("script_mode"):
            ctx.warn("No callable functions found; a script smoke check will be generated. It checks completion, not expected output or logic.")
        elif not a["functions"]:
            ctx.warn("No callable functions or executable top-level code were found to test.")
