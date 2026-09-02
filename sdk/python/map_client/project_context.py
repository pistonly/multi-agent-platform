"""不可变 ProjectContext — CLI 单点根解析（实验 e7244a91 / A1~A4）。

一次解析出四个根，之后所有 client / topic / experiment / sync / audit
模块只接受 context / workspace 参数，不再各自隐式调用 ``find_map_dir(None)``
或 ``Path.cwd()``：

- ``workspace_root``  代码仓库与 ``map/**`` 写入的唯一根目录
- ``map_dir``         ``workspace_root/.map``（workspace 内身份目录）
- ``content_root``    话题/实验内容根名（``config.yaml`` ``content_root``，默认 ``map``）
- ``config``          :class:`ProjectMapConfig`（身份/API 配置，来源见双根规则）
- ``persona``         当前请求的 persona 短名（未指定时 None）

双根显式化（A3）：``config_root`` 决定身份/API 配置来源，优先级
``--config-root`` > ``MAP_CONFIG_ROOT`` > ``workspace_root``。显式根
（前两级）不存在或缺 ``.map/config.yaml`` 时 fail closed，不回退 CWD
猜测；``config_root`` 绝不改变 ``map/**`` 写入根——写根永远随
``workspace_root``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from map_client.project_config import (
    CONFIG_FILE,
    MAP_DIR_NAME,
    ProjectMapConfig,
    find_map_dir,
    load_project_map_config,
    missing_map_config_message,
)

DEFAULT_CONTENT_ROOT = "map"


class ProjectRootNotFoundError(ValueError):
    """workspace_root 解析失败：未传 --project-root 且 CWD 向上找不到 .map/config.yaml。"""


class ConfigRootNotFoundError(ValueError):
    """显式 config 根（--config-root / MAP_CONFIG_ROOT）fail closed。"""


def config_root_missing_message(root: Path) -> str:
    return (
        f"Config root '{root}' has no {MAP_DIR_NAME}/{CONFIG_FILE}. "
        f"Pass --config-root pointing at a bootstrapped project (containing "
        f"{MAP_DIR_NAME}/{CONFIG_FILE}), or unset --config-root / MAP_CONFIG_ROOT "
        "to use the workspace root. No CWD fallback is attempted."
    )


def _require_explicit_config_root(root: Path) -> Path:
    """显式 config 根必须精确含 ``.map/config.yaml``，否则 fail closed。"""
    if root.is_dir() and (root / MAP_DIR_NAME / CONFIG_FILE).is_file():
        return root
    raise ConfigRootNotFoundError(config_root_missing_message(root))


def resolve_config_root(explicit: Path | None, workspace_root: Path) -> Path:
    """A3 三级优先级：--config-root > MAP_CONFIG_ROOT > workspace_root。

    前两级是「显式根」：不存在或缺 ``.map/config.yaml`` 即 raise（不向上
    搜索、不回退 CWD）；workspace_root 级别天然满足（解析 workspace 时
    已确认 ``.map/config.yaml`` 存在）。
    """
    if explicit is not None:
        return _require_explicit_config_root(explicit)
    env_root = os.environ.get("MAP_CONFIG_ROOT", "").strip()
    if env_root:
        return _require_explicit_config_root(Path(env_root))
    return workspace_root


def workspace_fingerprint(workspace_root: Path) -> str:
    """A4 workspace 根指纹：``st_dev + st_ino`` 锚点（workspace_root stat）。

    不得基于路径字符串：同一 workspace 经 macOS 卷挂载（``/Users/x`` 与
    ``/Volumes/x``）产生两个合法路径、realpath 不互相归一，路径比对会对
    合法 workspace 自我误报；stat 锚点同 inode 即同 workspace，且能区分
    同机多个 clone（内容锚 project_key+git origin 区分不了 split-brain）。

    已知限制：跨机器/网络文件系统的 dev/ino 不稳定——指纹不一致只代表
    「本机 stat 视角不同」。跨机器迁移必须走显式受审计的 rebind 流程，
    **任何 lifecycle 命令都不允许自动覆盖指纹**（写路径 fail closed 门禁
    归实验 B；本函数只服务读路径 warn）。
    """
    stat = workspace_root.stat()
    return f"dev={stat.st_dev}:ino={stat.st_ino}"


def fingerprint_warnings(
    local_fingerprint: str | None,
    server_workspace_path: str | None,
) -> list[str]:
    """A4 读路径 warn 规则（``map sync check`` 消费；只警告不阻断）。

    - server 未记录 workspace_path → 无 warn（投影模式无锚点可比）。
    - server workspace 本机不可 stat（跨机器/projection-cache）→ warn
      「无法证实 + rebind 限制」，呼应 dev/ino 机器本地性。
    - 可 stat 且指纹不同 → warn split-brain（疑似两个 clone）。
    - 同 inode 双挂载（路径不同、指纹相同）→ **不** warn（本方案核心价值）。
    """
    server_path = (server_workspace_path or "").strip()
    if not server_path:
        return []
    if local_fingerprint is None:
        return []
    server_root = Path(server_path)
    if not server_root.is_dir():
        return [
            f"[WARN] workspace fingerprint: server workspace '{server_path}' "
            "not stat-able on this machine — cannot verify (dev/ino is "
            "machine-local). Cross-machine moves must go through the "
            "explicit audited rebind flow; lifecycle commands must never "
            "auto-overwrite the fingerprint."
        ]
    server_fp = workspace_fingerprint(server_root)
    if server_fp == local_fingerprint:
        return []
    return [
        f"[WARN] workspace fingerprint mismatch: local={local_fingerprint} "
        f"server-workspace={server_fp} ({server_path}) — the server "
        "projection may belong to a different clone of this project "
        "(split-brain). Verify before any lifecycle write; rebind via "
        "the explicit audited flow only."
    ]


@dataclass(frozen=True)
class ProjectContext:
    """CLI 进程内一次性解析的不可变根上下文（A1）。"""

    workspace_root: Path
    config: ProjectMapConfig
    persona: str | None = None

    @property
    def map_dir(self) -> Path:
        """``workspace_root/.map``——workspace 内身份目录（写根随 workspace）。"""
        return self.workspace_root / MAP_DIR_NAME

    @property
    def config_root(self) -> Path:
        """身份/API 配置来源根（A3 双根显式化；默认 == workspace_root）。"""
        return self.config.map_dir.parent

    @property
    def content_root(self) -> str:
        """内容根名（``config.yaml`` ``content_root``，默认 ``map``）。"""
        return self.config.content_root or DEFAULT_CONTENT_ROOT

    @property
    def content_dir(self) -> Path:
        """``workspace_root/<content_root>``——``map/**`` 写入根。"""
        return self.workspace_root / self.content_root

    def workspace_fingerprint(self) -> str:
        return workspace_fingerprint(self.workspace_root)


def resolve_project_context(
    *,
    project_root: Path | None = None,
    config_root: Path | None = None,
    persona: str | None = None,
) -> ProjectContext:
    """单点解析：显式 ``--project-root`` 永远优先，未传时只从 CWD 向上查找一次。

    1. workspace_root：``find_map_dir(project_root)``（None → CWD 向上查找一次）；
       找不到 ``.map/config.yaml`` 即 fail closed（带 bootstrap 指引）。
    2. config_root：:func:`resolve_config_root` 三级优先级（显式根 fail closed）。
    3. config：从 config_root 加载 :class:`ProjectMapConfig`。
    """
    workspace_map_dir = find_map_dir(project_root)
    if workspace_map_dir is None:
        raise ProjectRootNotFoundError(missing_map_config_message())
    workspace_root = workspace_map_dir.parent
    resolved_config_root = resolve_config_root(config_root, workspace_root)
    config = load_project_map_config(project_root=resolved_config_root)
    return ProjectContext(
        workspace_root=workspace_root,
        config=config,
        persona=persona,
    )
