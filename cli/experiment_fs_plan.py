"""实验 plan 的 FS 物化 / 双写（实验 plan-db-content-retirement A1-1）。

自 ``cli/experiment_fs.py`` 拆出（模块 800 行上限，T46 行数守卫）。宿主
底部 re-export 这两个入口，既有 ``from cli.experiment_fs import …`` 引用
面不变；对本模块 helper（``workspace_root`` 等）走函数内 lazy import 反向
引用，避免与宿主底部的 re-export 形成导入环（同
``server/services/migration_manifest_execute.py`` 的拆分模式）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def materialize_experiment_plan(
    workspace: Path,
    slug: str,
    content: str,
    *,
    force: bool = False,
    content_root: str | None = None,
) -> tuple[Path, bool]:
    """Atomically persist an already-approved DB plan through the MAP CLI.

    ``--plan-file`` creation stores plan content in the lifecycle record.  This
    helper is the repair path for those records when a reviewer/executor also
    needs the normal FS-plane ``plan.md`` artifact.  It deliberately requires
    an existing experiment ``index.md`` and refuses a divergent overwrite
    unless the creator explicitly passes ``--force``.

    Returns ``(path, wrote)``; ``wrote=False`` means the identical artifact was
    already present.
    """
    from map_fs import experiment_index_path

    from cli.experiment_fs import content_root_name

    if not content.strip():
        raise ValueError("plan content is empty")
    root = content_root or content_root_name(workspace)
    index = experiment_index_path(workspace, slug, content_root=root)
    if not index.is_file():
        raise FileNotFoundError(f"experiment index not found for '{slug}': {index}; refuse to create an unbound plan")

    plan_path = index.parent / "plan.md"
    if plan_path.is_file():
        existing = plan_path.read_text(encoding="utf-8")
        if existing == content:
            return plan_path, False
        if not force:
            raise FileExistsError(f"plan already exists and differs: {plan_path}; re-run with --force to replace it")

    tmp = plan_path.with_suffix(".md.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, plan_path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return plan_path, True


def mirror_plan_to_fs(
    experiment: Any,
    *,
    content: str | None,
    workspace: Path | None = None,
) -> Path | None:
    """阶段 0 双写：把内联 ``--plan-file`` 接受的计划正文镜像到 FS ``plan.md``。

    CLI 双写让 ``--plan-file``（内联全文）create/revise 在照旧写 DB 全文的
    同时，也产出 FS-plane ``map/experiments/<slug>/plan.md`` 制品，使 flag
    ``plan_db_content_retired`` 开启前所有实验都已具备 FS 制品、随时可切
    （实验 plan-db-content-retirement A1-1）。

    复用 :func:`materialize_experiment_plan` 的原子写 + 幂等语义（同内容
    跳过），但**始终以 ``force=True`` 同步到 DB 接受的正文**：内联
    ``--plan-file`` 实验里 DB 才是事实源，``plan.md`` 本就是本镜像产出的
    制品，revise 的新正文必须传播到 FS，否则阶段 2/3 切 FS 权威时会读到陈旧
    正文。此处不复用 materialize 的「拒绝分歧覆盖」——那条保护是给「人工直接
    编辑过的 FS 制品」准备的，而阶段 0 尚未把 ``plan.md`` 提升为权威面，对
    内联实验而言「分歧」只可能是上一版镜像正文，覆盖它正是期望行为。
    frontmatter lint 仍由命令层前置把关（create 已 lint，revise 由 server
    硬校验），镜像层不重复校验。

    目标是投影后的实验目录：slug 由 ``plan_file_path`` 派生，缺失时回退到
    ``experiment.id`` 反查 FS 记录。无 workspace / 无实验目录 / 空 content
    一律**静默跳过**（阶段 0 是附加镜像，DB 写已成功，不能因镜像缺失把创建
    或修订整个打断）；返回写出的 ``plan.md`` 路径，或 ``None`` 表示未写。
    """
    from cli.experiment_fs import (
        content_root_name,
        find_fs_experiment,
        slug_from_plan_file_path,
        workspace_root,
    )

    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return None
    if not content or not content.strip():
        return None
    slug = slug_from_plan_file_path(getattr(experiment, "plan_file_path", None))
    if slug is None:
        fs = find_fs_experiment(root, ref=experiment.id)
        slug = fs.slug if fs is not None else None
    if slug is None:
        return None
    try:
        path, _wrote = materialize_experiment_plan(
            root,
            slug,
            content,
            force=True,
            content_root=content_root_name(root),
        )
    except FileNotFoundError:
        # 无 index.md 绑定：拒绝凭空造目录，不阻断已成功的 DB 写。
        return None
    return path
