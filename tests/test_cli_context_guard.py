"""实验 e7244a91（A5）：CLI 隐式 workspace 解析静态守卫。

参照 ``lib/red_line_clause.py`` + 副本漂移检测的惯例（约定文本化为可执行
断言）：``cli/`` 下**禁止**出现隐式 workspace 解析调用——

- ``find_map_dir(...)``（含 ``xxx.find_map_dir(...)`` 属性调用）
- ``Path.cwd()``

唯一收口点是 ``cli/project_context.py``：

- workspace 单次向上查找经运行时 ``cli.main.find_map_dir``（注入面）
- ``default_bootstrap_root()`` 是唯一 ``Path.cwd()`` 落点（bootstrap 是
  「创建 ``.map/`` 的目标根」，不是 workspace 解析）

守卫基于 AST（不是正则）：docstring / 注释里的「不得这样做」示例文案
不会误报。守卫 CI 绿是实验 A 完成判据（A5 / plan frontmatter）。
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parent.parent / "cli"

# 唯一收口模块：单点解析 + bootstrap 目标根（见模块 docstring）。
SANCTIONED_FILES = {"project_context.py"}


def _cli_python_files() -> list[Path]:
    return sorted(
        path
        for path in CLI_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _implicit_resolution_hits(tree: ast.AST) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "find_map_dir" or isinstance(func, ast.Attribute) and func.attr == "find_map_dir":
            hits.append(("find_map_dir", node.lineno))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "cwd"
            and isinstance(func.value, ast.Name)
            and func.value.id == "Path"
        ):
            hits.append(("Path.cwd", node.lineno))
    return hits


def test_cli_has_no_implicit_workspace_resolution() -> None:
    offenders: list[str] = []
    for path in _cli_python_files():
        if path.name in SANCTIONED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for kind, lineno in _implicit_resolution_hits(tree):
            offenders.append(
                f"{path.relative_to(CLI_ROOT.parent)}:{lineno} {kind}() — "
                "use cli.project_context.current_context() instead"
            )
    assert offenders == [], (
        "cli/ modules must not resolve the workspace implicitly "
        "(experiment e7244a91 A5):\n" + "\n".join(offenders)
    )


def test_sanctioned_resolution_point_is_single() -> None:
    """收口模块存在，且确实是唯一含 Path.cwd() / find_map_dir 调用的文件。"""
    sanctioned = CLI_ROOT / "project_context.py"
    assert sanctioned.is_file(), "cli/project_context.py is the single resolution point"
    tree = ast.parse(sanctioned.read_text(encoding="utf-8"), filename=str(sanctioned))
    kinds = {kind for kind, _ in _implicit_resolution_hits(tree)}
    assert {"find_map_dir", "Path.cwd"} <= kinds, (
        "sanctioned module must still host both resolution primitives; "
        "if the mechanism moved, update SANCTIONED_FILES deliberately"
    )


def test_context_module_exposes_required_surface() -> None:
    """守卫的存在前提：收口 API 可导入（防止重构把入口删掉而守卫空转）。"""
    from cli.project_context import (  # noqa: F401
        current_context,
        default_bootstrap_root,
        identity_root,
        optional_context,
    )
