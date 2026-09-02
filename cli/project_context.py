"""CLI 进程内 ProjectContext 单点解析（实验 e7244a91 / A1~A3）。

cli/ 命令模块不再各自调用 ``find_map_dir(None)`` / ``Path.cwd()`` 隐式解析
workspace，统一经 :func:`current_context` 拿一次性解析好的不可变上下文。

解析时机：进程内首次访问时解析并缓存（等价「入口一次性解析」——同一次
CLI 调用内 workspace 解析只发生一次）。缓存键含 ``os.getcwd()``：同进程
内 chdir（测试场景）自动失效重解析，单次调用内 cwd 不变故恒为一次。

注入面（与 T23 同理）：``find_map_dir`` / ``load_project_map_config`` 经
运行时 ``from cli import main`` 解析——测试 monkeypatch ``cli.main.find_map_dir``
等入口在重构后继续生效。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from map_client.project_config import missing_map_config_message
from map_client.project_context import (
    ConfigRootNotFoundError,
    ProjectContext,
    ProjectRootNotFoundError,
    resolve_config_root,
)

__all__ = [
    "ConfigRootNotFoundError",
    "ProjectRootNotFoundError",
    "current_context",
    "default_bootstrap_root",
    "identity_root",
    "optional_context",
    "reset_context_cache",
]


def _cli_options() -> dict[str, Any]:
    """Runtime-resolve ``cli.main._cli_options``（monkeypatch 面，同 cli.runner）。"""
    from cli import main as _main

    return _main._cli_options


# 缓存：(cache_key, context)。键含全局选项与 cwd，见模块 docstring。
_cached: tuple[tuple[Any, ...], ProjectContext] | None = None


def reset_context_cache() -> None:
    """测试用：丢弃已缓存上下文（正常 CLI 进程无需调用）。"""
    global _cached
    _cached = None


def _cache_key() -> tuple[Any, ...]:
    opts = _cli_options()
    # 注入面函数对象与 MAP_CONFIG_ROOT 一并入键：测试同进程内 monkeypatch
    # 切换 find_map_dir 指向不同 tmp workspace 时自动失效重解析；生产进程
    # 内这些输入恒定，缓存照常命中。
    from cli import main as _main

    return (
        opts.get("project_root"),
        opts.get("config_root"),
        os.getcwd(),
        os.environ.get("MAP_CONFIG_ROOT"),
        _main.find_map_dir,
        _main.load_project_map_config,
    )


def _resolve_context(project_root: Path | None, config_root: Path | None, persona: str | None) -> ProjectContext:
    """单点解析流水线（走 cli.main 注入面，见模块 docstring）。"""
    from cli import main as _main  # test injection surface: cli.main.find_map_dir / load_project_map_config

    map_dir = _main.find_map_dir(project_root)
    if map_dir is None:
        raise ProjectRootNotFoundError(missing_map_config_message())
    workspace_root = map_dir.parent
    resolved_config_root = resolve_config_root(config_root, workspace_root)
    config = _main.load_project_map_config(project_root=resolved_config_root)
    return ProjectContext(workspace_root=workspace_root, config=config, persona=persona)


def current_context() -> ProjectContext:
    """一次性解析并缓存 ProjectContext；失败抛 ProjectRootNotFoundError。"""
    global _cached
    key = _cache_key()
    if _cached is not None and _cached[0] == key:
        return _cached[1]
    opts = _cli_options()
    context = _resolve_context(
        project_root=opts.get("project_root"),
        config_root=opts.get("config_root"),
        persona=opts.get("persona"),
    )
    _cached = (key, context)
    return context


def optional_context() -> ProjectContext | None:
    """可降级版：workspace/config 解析或 config 加载失败时返回 None。

    宽容所有 ``ValueError``（含 config.yaml 缺 project_key 等加载失败），
    对齐调用方（``_resolve_project`` / audit / routing 合并视角）原有的
    「解析不出就降级」语义；严格版见 :func:`current_context`。
    """
    try:
        return current_context()
    except ValueError:
        return None


def identity_root() -> Path | None:
    """身份/API 配置来源根（A3）：--config-root > MAP_CONFIG_ROOT > --project-root。

    只决定 client/token/api_url 从哪个 ``.map/config.yaml`` 加载，绝不改变
    ``map/**`` 写入根。显式根（前两级）不存在或缺 ``.map/config.yaml`` 时
    fail closed（不向上搜索、不回退 CWD）；返回 None 时
    :func:`map_client.project_config.resolve_client` 保持既有
    MAP_TOKEN / ``~/.map`` 兜底语义。
    """
    opts = _cli_options()
    explicit = opts.get("config_root")
    if explicit is not None:
        return _require_explicit(Path(explicit))
    env_root = os.environ.get("MAP_CONFIG_ROOT", "").strip()
    if env_root:
        return _require_explicit(Path(env_root))
    project_root = opts.get("project_root")
    return Path(project_root) if project_root is not None else None


def _require_explicit(root: Path) -> Path:
    from map_client.project_context import _require_explicit_config_root

    return _require_explicit_config_root(root)


def default_bootstrap_root() -> Path:
    """创建型命令（bootstrap）的默认目标根：CWD。

    这是唯一被守卫允许的 ``Path.cwd()`` 落点（cli/project_context.py）：
    bootstrap 语义是「在目标根**创建** ``.map/``」，此时不存在可解析的
    workspace——CWD 是创建默认值，不是隐式 workspace 解析。显式
    ``--project-root`` 永远优先。
    """
    return Path.cwd()
