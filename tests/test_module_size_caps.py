"""单文件 800 行上限回归守卫（T45）。

背景：T33 给 ``cli/main.py`` 加了 800 行 cap（tests/cli/test_compat.py），
但其余超限文件未覆盖；T45 把全仓 10 个超 800 行的模块全部拆分到上限
以下。本测试把原始宿主与本次拆出的子模块统一登记，防止任何文件再长
回来——超过上限时显式失败并提示继续拆分（与 T33 的处理一致：有意增长
需 bump 上限并在 commit 说明理由）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MAX_LINES = 800

# (repo-relative path, cap 说明)
CAPPED_FILES: list[str] = [
    # --- cli ---
    "cli/simple_waker.py",
    "cli/waker_context.py",
    "cli/waker_checks.py",
    "cli/waker_state.py",
    "cli/commands/experiment.py",
    "cli/commands/experiment_lifecycle.py",
    "cli/commands/experiment_inspect.py",
    "cli/commands/topic.py",
    "cli/commands/topic_migrate.py",
    "cli/commands/topic_view.py",
    "cli/commands/mention.py",
    "cli/commands/todo.py",
    "cli/commands/fs.py",
    "cli/fs_write_flow.py",
    "cli/fs_sync.py",
    # --- sdk ---
    "sdk/python/map_client/client.py",
    "sdk/python/map_client/client_mixins/agent_project.py",
    "sdk/python/map_client/client_mixins/experiment.py",
    "sdk/python/map_client/client_mixins/fs_topic.py",
    "sdk/python/map_client/client_mixins/todo_notification.py",
    "sdk/python/map_fs/model.py",
    "sdk/python/map_fs/frontmatter.py",
    "sdk/python/map_fs/action_items.py",
    "sdk/python/map_fs/topic_parser.py",
    "sdk/python/map_fs/index_io.py",
    # --- server ---
    "server/domain/models/__init__.py",
    "server/domain/models/project.py",
    "server/domain/models/experiment.py",
    "server/domain/models/topic.py",
    "server/domain/models/audit.py",
    "server/domain/models/notification.py",
    "server/domain/models/fs.py",
    "server/services/fs_source_service.py",
    "server/services/fs_plane_loader.py",
    "server/services/fs_topic_view.py",
    "server/services/notification_service.py",
    "server/services/notification_inbox.py",
    "server/services/phase_service.py",
    "server/services/phase_completion.py",
    # --- T46 新增登记 ---
    # 这两个是 2026-09-14 体检发现的「白名单逃逸」：migration_manifest_service
    # 1209 行（2026-09-05 新建）、runner.py 809 行，都没进过本清单。现已各自
    # 拆分到上限以下并登记（拆分产物一并登记）。
    "server/services/migration_manifest_service.py",
    "server/services/migration_manifest_stale.py",
    "server/services/migration_manifest_lkg.py",
    "server/services/migration_manifest_execute.py",
    "cli/runner.py",
    "cli/runner_resolve.py",
]

# ---------------------------------------------------------------------------
# 全仓扫描（T46）：只查白名单会让「新建的大文件」天然逃逸——上面那两个就是
# 这样长到 1209 行没人知道的。这里补一道全量扫描兜底，与 mypy 门禁同一类洞。
# ---------------------------------------------------------------------------
SCAN_ROOTS: tuple[str, ...] = ("cli", "server", "sdk/python", "lib")
SCAN_EXCLUDE_PARTS: frozenset[str] = frozenset({"__pycache__", "_migrate"})


def _iter_prod_modules() -> list[str]:
    rel_paths: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if SCAN_EXCLUDE_PARTS & set(path.parts):
                continue
            rel_paths.append(path.relative_to(REPO_ROOT).as_posix())
    return rel_paths


@pytest.mark.parametrize("rel_path", _iter_prod_modules())
def test_no_unregistered_oversized_module(rel_path: str) -> None:
    """全仓兜底：任何生产模块超过 800 行即失败（不问是否登记过）。"""
    actual = sum(1 for _ in (REPO_ROOT / rel_path).open(encoding="utf-8"))
    assert actual <= MAX_LINES, (
        f"{rel_path} is {actual} lines (cap={MAX_LINES}) — 拆到上限以下，"
        "或在本文件显式登记并说明理由。"
    )


@pytest.mark.parametrize("rel_path", CAPPED_FILES)
def test_module_under_size_cap(rel_path: str) -> None:
    """Monolith regression guard: listed modules must stay under 800 lines."""
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"capped file missing: {rel_path}"
    actual = sum(1 for _ in path.open(encoding="utf-8"))
    assert actual <= MAX_LINES, (
        f"{rel_path} grew to {actual} lines (cap={MAX_LINES}); "
        "consider splitting it further. Intentional growth requires bumping "
        "the cap here plus a commit note explaining why."
    )
