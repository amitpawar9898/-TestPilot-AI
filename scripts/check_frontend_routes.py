"""Compare API route builders referenced by the React UI with the generated backend OpenAPI."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"
OPENAPI = ROOT / "frontend" / "openapi.json"
ROUTES = FRONTEND / "api" / "routes.ts"
VIEW_FILES = [FRONTEND / "ui" / "App.tsx", FRONTEND / "ui" / "Page.tsx"]
PARAM_NAMES = {"runId": "run_id", "batchId": "batch_id", "fixId": "fix_id", "userId": "user_id"}


def main() -> int:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    source = ROUTES.read_text(encoding="utf-8")
    stack: list[tuple[int, str]] = []
    declared: dict[str, tuple[str, str]] = {}
    for line in source.splitlines():
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        block = re.match(r"(\w+): \{\s*$", line.strip())
        if block:
            stack.append((indent, block.group(1)))
            continue
        key = re.match(r"(\w+):\s*(.*)$", line.strip())
        if not key:
            continue
        name, value = key.groups()
        method = re.search(r"ApiPath<'(get|post|patch|put|delete)'>", value)
        if not method:
            continue
        literal = re.search(r"'([^']+)'\s+satisfies ApiPath", value)
        template = re.search(r"=> `([^`]+)`", value)
        path = literal.group(1) if literal else template.group(1) if template else None
        if not path:
            continue
        path = re.sub(r"\$\{(?:id\()?([A-Za-z_]+)(?:\))?\}",
                      lambda m: "{" + PARAM_NAMES.get(m.group(1), m.group(1)) + "}", path)
        path = path.split("?", 1)[0]
        full_name = ".".join([*(part for _, part in stack), name])
        declared[full_name] = (method.group(1).upper(), "/api" + path)

    ui = "\n".join(file.read_text(encoding="utf-8") for file in VIEW_FILES)
    used = set(re.findall(r"routes\.([A-Za-z_]+(?:\.[A-Za-z_]+)*)", ui))
    unknown_names = sorted(used - declared.keys())
    operations = {
        (method.upper(), path)
        for path, methods in spec["paths"].items()
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    mismatches = [(name, *declared[name]) for name in sorted(used)
                  if name in declared and declared[name] not in operations]
    used_ops = {declared[name] for name in used if name in declared}
    unused_builders = sorted(set(declared) - used)
    unused_backend = sorted(operations - used_ops)

    print(f"frontend_routes={len(declared)} used_by_ui={len(used_ops)} backend_operations={len(operations)}")
    print(f"calls_without_builder={len(unknown_names)} method_or_path_mismatches={len(mismatches)}")
    for name in unknown_names:
        print(f"NO BUILDER {name}")
    for name, method, path in mismatches:
        print(f"MISMATCH {name}: {method} {path}")
    print(f"unused_frontend_builders={unused_builders}")
    print(f"backend_operations_not_called={len(unused_backend)}")
    for method, path in unused_backend:
        print(f"UNUSED {method} {path} (not exposed as a current React action; endpoint remains verified by the HTTP script)")
    return 1 if unknown_names or mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
