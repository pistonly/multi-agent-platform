#!/usr/bin/env python3
"""SSE handler import isolation (acceptance A).

Scans server/services/notification_stream.py for import violations.
Exit 0 when clean; non-zero when a forbidden import is detected.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "server" / "services" / "notification_stream.py"

ALLOWED_SERVER_SERVICE_PREFIXES = (
    "server.services.topics",
    "server.services.experiments",
    "server.services.comments",
    "server.services.mentions",
)

ALLOWED_THIRD_PARTY = frozenset({"fastapi", "starlette"})


def _module_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name
    if isinstance(node, ast.ImportFrom) and node.module:
        return node.module
    return None


def forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        mod = _module_name(node)
        if not mod:
            continue
        if mod.startswith("server.services."):
            if not any(mod == prefix or mod.startswith(prefix + ".") for prefix in ALLOWED_SERVER_SERVICE_PREFIXES) and mod != "server.services.notification_stream":
                violations.append(mod)
            continue
        if mod.startswith("server."):
            violations.append(mod)
            continue
        root = mod.split(".", 1)[0]
        if root in {"fastapi", "starlette"}:
            continue
        if root in sys.stdlib_module_names:
            continue
        violations.append(mod)
    return sorted(set(violations))


def main() -> int:
    if not TARGET.is_file():
        print(f"missing SSE handler module: {TARGET}", file=sys.stderr)
        return 2
    bad = forbidden_imports(TARGET)
    if bad:
        print("forbidden imports in notification_stream.py:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
