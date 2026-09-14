"""A1-1（阶段 0 CLI 双写）：``mirror_plan_to_fs`` 把内联 ``--plan-file`` 的
计划正文镜像到 FS ``plan.md``，DB 写行为不变。

Pins（实验 plan-db-content-retirement / 验收 A1-1）：

1. 有 index.md 绑定 + plan_file_path → 写出 ``plan.md``，重复调用幂等。
2. 内联实验 DB 是事实源：现存 ``plan.md`` 与新正文分歧 → 同步为 DB 接受的
   新正文（否则阶段 2/3 切 FS 权威时读到陈旧版本）。
3. 无 workspace / 空 content / 无 FS 绑定 → 静默跳过返回 None，绝不抛错。
4. ``plan_file_path`` 缺失时按 experiment id 反查 FS 投影解析 slug。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from map_fs import experiment_id_for_slug, write_experiment_index

from cli.experiment_fs import mirror_plan_to_fs

_PLAN_V1 = "---\ntitle: Mirror\nacceptance:\n  - a\nevidence_keys:\n  - e\ndependencies:\n  - d\n---\n\n# Plan v1\n"
_PLAN_V2 = "---\ntitle: Mirror\nacceptance:\n  - a\nevidence_keys:\n  - e\ndependencies:\n  - d\n---\n\n# Plan v2\n"


def _bind_experiment(workspace: Path, slug: str, *, projection_id: uuid.UUID | None = None) -> Path:
    write_experiment_index(
        workspace,
        slug,
        title="Mirror",
        creator="host",
        phase="draft",
        current_plan_version=1,
        executor="",
        topic="t",
        projection_id=projection_id,
    )
    return workspace / "map" / "experiments" / slug


def test_inline_create_mirrors_plan_to_fs(tmp_path: Path) -> None:
    """plan_file_path 派生 slug → 写出 plan.md；重复调用幂等（wrote=False 也返回路径）。"""
    exp_dir = _bind_experiment(tmp_path, "double-write")
    exp = SimpleNamespace(
        id=experiment_id_for_slug("double-write"),
        plan_file_path="map/experiments/double-write/plan.md",
    )

    path = mirror_plan_to_fs(exp, content=_PLAN_V1, workspace=tmp_path)
    assert path is not None
    assert path == exp_dir / "plan.md"
    assert path.read_text(encoding="utf-8") == _PLAN_V1

    # 幂等：同内容再镜像不报错、内容不变。
    again = mirror_plan_to_fs(exp, content=_PLAN_V1, workspace=tmp_path)
    assert again == path
    assert path.read_text(encoding="utf-8") == _PLAN_V1

    # revise 双写：新正文覆盖同一路径。
    revised = mirror_plan_to_fs(exp, content=_PLAN_V2, workspace=tmp_path)
    assert revised == path
    assert path.read_text(encoding="utf-8") == _PLAN_V2


def test_divergent_plan_syncs_to_db_authority(tmp_path: Path) -> None:
    """阶段 0 内联实验 DB 是事实源：现存 plan.md 与 DB 接受正文分歧 → 同步为
    DB 新正文（避免阶段 2/3 切 FS 权威时读到陈旧版本）。"""
    exp_dir = _bind_experiment(tmp_path, "divergent")
    plan_md = exp_dir / "plan.md"
    plan_md.write_text(_PLAN_V1, encoding="utf-8")

    exp = SimpleNamespace(
        id=experiment_id_for_slug("divergent"),
        plan_file_path="map/experiments/divergent/plan.md",
    )
    path = mirror_plan_to_fs(exp, content=_PLAN_V2, workspace=tmp_path)
    assert path == plan_md
    assert plan_md.read_text(encoding="utf-8") == _PLAN_V2


def test_unbound_or_empty_inputs_skip_silently(tmp_path: Path) -> None:
    """无 FS 绑定 / 空 content → None，不抛错（阶段 0 镜像不阻断 DB 写已成功的流程）。"""
    # 无 index.md 绑定且无 ref 命中：拒绝推断目标目录。
    exp = SimpleNamespace(id=uuid.uuid4(), plan_file_path=None)
    assert mirror_plan_to_fs(exp, content=_PLAN_V1, workspace=tmp_path) is None

    # content 为空 / None：直接跳过（materialize 层本会抛 ValueError）。
    exp2 = SimpleNamespace(
        id=experiment_id_for_slug("empty-skip"),
        plan_file_path="map/experiments/empty-skip/plan.md",
    )
    assert mirror_plan_to_fs(exp2, content=None, workspace=tmp_path) is None
    assert mirror_plan_to_fs(exp2, content="   ", workspace=tmp_path) is None

    # 无 workspace：None（不触碰文件系统）。
    assert mirror_plan_to_fs(exp, content=_PLAN_V1, workspace=None) is None
    assert not (tmp_path / "map" / "experiments" / "empty-skip" / "plan.md").exists()


def test_slug_falls_back_to_fs_ref_when_plan_file_path_missing(tmp_path: Path) -> None:
    """create 响应缺 plan_file_path（FS overlay 前）→ 按 id 反查投影解析 slug。"""
    projection = uuid.uuid4()
    exp_dir = _bind_experiment(tmp_path, "ref-fallback", projection_id=projection)
    exp = SimpleNamespace(id=projection, plan_file_path=None)

    path = mirror_plan_to_fs(exp, content=_PLAN_V1, workspace=tmp_path)
    assert path == exp_dir / "plan.md"
    assert path.read_text(encoding="utf-8") == _PLAN_V1
