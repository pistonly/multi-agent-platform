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
]


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
