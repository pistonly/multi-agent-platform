"""写入：纯文件操作（CLI 与服务端验证型写共用）。

验证型写统一模式：写前校验契约（required 字段 / phase 允许边 /
expected_from 防手改），写后回读复核；失败不落盘或回滚原文件。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from map_fs.frontmatter import (
    _FM_FENCE,
    _coerce_participants,
    _parse_dt,
    _require_slug,
    parse_front_matter,
)
from map_fs.model import (
    _EXPERIMENT_DIRECT_EDGES,
    _EXPERIMENT_STANDARD_EDGES,
    DEFAULT_CONTENT_ROOT,
    EXPERIMENT_INDEX_REQUIRED,
    EXPERIMENT_PHASES,
    ExperimentIndexError,
)


def _render_file(meta: dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(
        {k: v for k, v in meta.items() if v is not None},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _created_at_equal(a: str, b: str) -> bool:
    """ISO-8601 字符串宽松比较：去尾随零 + 视 ``+00:00`` / ``Z`` 等价。

    YAML 反序列化会把 ``2026-08-30T21:58:56.109180+00:00`` 解析为 datetime 对象,
    而 CLI 传回的是字符串。直接 ``str(a) == str(b)`` 在 datetime 上展开成
    ``repr`` 格式（含空格、T 分隔符）会与 ISO 字符串不匹配。本函数规范化
    两端为 ISO-8601 字符串后再比较，避免假阳性 diff。
    """
    from datetime import datetime as _dt

    def _to_iso(value: object) -> str:
        if isinstance(value, _dt):
            return value.isoformat()
        try:
            d = _dt.fromisoformat(str(value).replace("Z", "+00:00"))
            return d.isoformat()
        except (TypeError, ValueError):
            return str(value).strip()

    return _to_iso(a) == _to_iso(b)


def write_topic_index(
    workspace: Path,
    slug: str,
    *,
    title: str,
    creator: str,
    description: str = "",
    status: str = "open",
    round_: int | str = 1,
    participants: list[str] | None = None,
    content_root: str = DEFAULT_CONTENT_ROOT,
    overwrite: bool = False,
    created_at: str | None = None,
) -> Path:
    """创建（或显式 ``overwrite=True`` 覆盖）话题文件夹与 index.md。返回 index.md 路径。

    冲突检测：``map/topics/<slug>/index.md`` 已存在且 ``overwrite=False``
    → 抛 :class:`FileExistsError`（文案含 "fs topic '<slug>' already exists"）。
    CLI ``topic create`` 捕获该异常后 exit 1 + stderr 含 "already exists"。

    ``overwrite=True`` 保留语义：
    - 评论文件（``round<N>-*.md``）全部保留（不动）
    - ``created_at`` 不变（即使 ``--force`` 也禁止覆盖）；若调用方同时传
      ``created_at`` 参数且与旧值不同 → 抛 :class:`ValueError`
      "created_at is immutable, use `map topic amend --created-at`"
    - ``experiments`` 关联列表按 append 语义合并（不重置）

    ``participants`` 为参与人白名单（declared），写入 front-matter；
    creator 始终隐含在内（不强制写入列表）。
    """
    slug = _require_slug(slug)
    topic_dir = workspace / content_root / "topics" / slug
    index_path = topic_dir / "index.md"
    if index_path.exists() and not overwrite:
        raise FileExistsError(
            f"fs topic '{slug}' already exists at {index_path} "
            "(pass overwrite=True or use --force to replace)"
        )
    round_str = round_ if isinstance(round_, str) else f"round{round_}"
    meta: dict[str, object] = {
        "title": title,
        "status": status,
        "round": round_str,
        "creator": creator,
        "created_at": (datetime.now(timezone.utc).isoformat() if not index_path.exists() else None),
        "description": description or None,
    }
    old_meta: dict[str, Any] = {}
    if meta["created_at"] is None:  # 已存在 index：保留原 created_at
        old_meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
        meta["created_at"] = old_meta.get("created_at")
    # created_at 硬约束（实验 cli-fs-topic-lifecycle-invariants A3）：
    # overwrite 路径下，CLI 显式传 created_at 且与旧值不同 → 拒绝偷渡。
    if created_at is not None and index_path.exists():
        old_created = old_meta.get("created_at")
        if old_created is not None and not _created_at_equal(created_at, old_created):
            raise ValueError(
                f"created_at is immutable, use `map topic amend --created-at` "
                f"(old={old_created}, attempted={created_at})"
            )
        meta["created_at"] = created_at
    # participants：显式传入优先；未传且旧 index 已有 → 保留旧声明
    declared = _coerce_participants(participants)
    if not declared:
        declared = _coerce_participants(old_meta.get("participants"))
    if declared:
        meta["participants"] = declared
    # experiments 关联列表 append 合并（A2）：保留旧条目，新条目按 id 去重追加
    old_experiments = old_meta.get("experiments") if old_meta else None
    if old_experiments:
        # 由调用方传新列表时按 id 合并；本次 cli topic create 不传 experiments
        # 字段（实验 create 命令通过其他路径回写），保持旧列表不动
        meta["experiments"] = old_experiments
    body = f"# {title}\n\n{description}".rstrip() + "\n"
    _atomic_write(index_path, _render_file(meta, body))
    return index_path


def _reject_embedded_frontmatter(body: str) -> None:
    """写路径前置校验（fs-write-entry-validation W1）：body 自带 frontmatter → 拒绝。

    frontmatter（author/round/posted_at 三机器字段）由 ``write_round_comment``
    生成；body 再带一份会被静默埋进正文，产生「看似生效实则无效」的隐匿脏数据
    （实测脏 fixture ``posted_at: '$ts'`` 即模板未渲染走旁路入库）。判定口径：
    body 以 ``---`` 围栏开头且可解析为 YAML mapping 且含三机器字段任一键才拒；
    纯 Markdown 分隔线/无关 YAML 块放行，不误伤。
    """
    stripped = body.lstrip()
    if not stripped.startswith(_FM_FENCE):
        return
    meta, _ = parse_front_matter(stripped)
    machine_keys = {"author", "round", "posted_at"} & set(meta)
    if meta and machine_keys:
        raise ValueError(
            "body must not carry its own frontmatter; author/round/posted_at "
            f"are generated by the CLI (got keys: {sorted(machine_keys)}). "
            "正确形态：正文直接以标题或文字开头，例如 '# 我的发言'。"
        )


def write_round_comment(
    workspace: Path,
    slug: str,
    *,
    round_number: int,
    persona: str,
    body: str,
    kind: str = "user",
    is_round_summary: bool = False,
    content_root: str = DEFAULT_CONTENT_ROOT,
    overwrite: bool = False,
) -> Path:
    """写入一条评论文件 round<N>-<persona>.md（每轮每人一个文件）。

    文件已存在且 overwrite=False 时抛 FileExistsError（immutable 约定）。
    body 自带 frontmatter（author/round/posted_at 任一键）时抛 ValueError
    （W1 前置校验，--force 不豁免——overwrite 只豁免 immutable 约定）。
    发言人不在 index.md 的 participants 白名单时自动并入（发言即参与）；
    index.md 缺失（手建文件夹）时跳过并入，不影响评论写入。
    """
    _reject_embedded_frontmatter(body)
    slug = _require_slug(slug)
    topic_dir = workspace / content_root / "topics" / slug
    comment_path = topic_dir / f"round{round_number}-{persona}.md"
    if comment_path.exists() and not overwrite:
        raise FileExistsError(
            f"comment file already exists (immutable convention): {comment_path}"
        )
    meta = {
        "author": persona,
        "round": round_number,
        "kind": kind,
        "is_round_summary": is_round_summary or None,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(comment_path, _render_file(meta, body))
    _merge_participant(workspace, slug, persona, content_root=content_root)
    return comment_path


def _merge_participant(workspace: Path, slug: str, persona: str, *, content_root: str) -> None:
    """把新发言人并入 index.md 的 participants 白名单（已在内则不动）。"""
    index_path = workspace / content_root / "topics" / slug / "index.md"
    if not index_path.is_file():
        return  # 手建文件夹无 index：跳过并入（评论文件已写入，发言仍有效）
    meta, body = parse_front_matter(index_path.read_text(encoding="utf-8"))
    declared = _coerce_participants(meta.get("participants"))
    creator = str(meta.get("creator") or "host")
    if persona in declared or persona == creator:
        return
    declared.append(persona)
    meta["participants"] = declared
    _atomic_write(index_path, _render_file(meta, body))


def experiment_index_path(
    workspace: Path, slug: str, *, content_root: str = DEFAULT_CONTENT_ROOT
) -> Path:
    return workspace / content_root / "experiments" / _require_slug(slug) / "index.md"


def validate_experiment_index_meta(meta: dict[str, Any]) -> None:
    """独立可调用：校验 index.md 契约字段与 phase 枚举。非法则不落盘。

    不读取 DB。手改 ``phase: done`` 本身若字段齐全会通过本函数；
    ``commit_experiment_index_write`` 再用 expected_from 拦截绕过 CLI 的相位篡改。
    """
    missing: list[str] = []
    for key in EXPERIMENT_INDEX_REQUIRED:
        if key not in meta or meta[key] is None:
            missing.append(key)
            continue
        # executor/topic 在 start 前可为空串，但仍须显式出现在 front-matter。
        if key not in ("executor", "topic") and meta[key] == "":
            missing.append(key)
    if missing:
        raise ExperimentIndexError(
            f"experiment index.md missing required fields: {', '.join(missing)}"
        )
    phase = str(meta.get("phase") or "").lower()
    if phase not in EXPERIMENT_PHASES:
        raise ExperimentIndexError(
            f"experiment index.md has illegal phase {phase!r}; "
            f"allowed: {', '.join(sorted(EXPERIMENT_PHASES))}"
        )
    try:
        version = int(meta["current_plan_version"])
    except (TypeError, ValueError) as exc:
        raise ExperimentIndexError(
            f"experiment index.md current_plan_version must be int, got {meta.get('current_plan_version')!r}"
        ) from exc
    if version < 1:
        raise ExperimentIndexError("experiment index.md current_plan_version must be >= 1")
    if _parse_dt(meta.get("updated_at")) is None:
        raise ExperimentIndexError("experiment index.md updated_at missing or unparseable")


def validate_experiment_phase_transition(
    current: str, target: str, *, mode: str = "standard"
) -> None:
    current_l = str(current).lower()
    target_l = str(target).lower()
    if current_l not in EXPERIMENT_PHASES:
        raise ExperimentIndexError(f"illegal current phase {current!r}")
    if target_l not in EXPERIMENT_PHASES:
        raise ExperimentIndexError(f"illegal target phase {target!r}")
    table = _EXPERIMENT_DIRECT_EDGES if mode == "direct" else _EXPERIMENT_STANDARD_EDGES
    allowed = table.get(current_l, frozenset())
    if target_l == current_l:
        return
    if target_l not in allowed:
        raise ExperimentIndexError(
            f"illegal experiment phase write {current_l} -> {target_l} (mode={mode})"
        )


def validate_experiment_index_file(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
    expected_from_phase: str | None = None,
    target_phase: str | None = None,
    mode: str = "standard",
) -> dict[str, Any]:
    """独立可调用的 index.md validate（A2）。返回当前 front-matter。

    ``expected_from_phase``：写回前的期望相位。与文件不一致 ⇒ 判定为手改，拒绝。
    ``target_phase``：若给出，再校验允许边。
    """
    index_path = experiment_index_path(workspace, slug, content_root=content_root)
    if not index_path.is_file():
        raise ExperimentIndexError(f"experiment index not found: {index_path}")
    meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
    validate_experiment_index_meta(meta)
    current = str(meta["phase"]).lower()
    if expected_from_phase is not None and current != str(expected_from_phase).lower():
        raise ExperimentIndexError(
            f"experiment index.md phase is {current!r}, expected {expected_from_phase!r} "
            "(hand-edited phase is rejected; use map experiment CLI)"
        )
    if target_phase is not None:
        validate_experiment_phase_transition(current, target_phase, mode=mode)
    return meta


def write_experiment_index(
    workspace: Path,
    slug: str,
    *,
    title: str,
    creator: str,
    phase: str = "draft",
    current_plan_version: int = 1,
    executor: str = "",
    topic: str = "",
    description: str = "",
    projection_id: uuid.UUID | str | None = None,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """创建（或覆盖）实验文件夹与 index.md。写前校验契约，写后回读。"""
    slug = _require_slug(slug)
    now = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "title": title,
        "phase": str(phase).lower(),
        "current_plan_version": int(current_plan_version),
        "creator": creator,
        "executor": executor if executor is not None else "",
        "topic": topic if topic is not None else "",
        "updated_at": now,
        "description": description or None,
    }
    if projection_id is not None:
        meta["projection_id"] = str(projection_id)
    index_path = experiment_index_path(workspace, slug, content_root=content_root)
    if index_path.is_file():
        old_meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
        if old_meta.get("created_at"):
            meta["created_at"] = old_meta["created_at"]
        elif old_meta.get("projection_id") and projection_id is None:
            meta["projection_id"] = old_meta["projection_id"]
    else:
        meta["created_at"] = now
    validate_experiment_index_meta(meta)
    body = f"# {title}\n\n{description}".rstrip() + "\n"
    _atomic_write(index_path, _render_file(meta, body))
    written, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
    validate_experiment_index_meta(written)
    if str(written.get("phase")).lower() != meta["phase"]:
        index_path.unlink(missing_ok=True)
        raise ExperimentIndexError("write-back re-read phase mismatch; file not kept")
    return index_path


def update_experiment_index(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
    mode: str = "standard",
    **fields: object,
) -> Path:
    """合并更新 index.md。若改 phase，必须是允许边；写后回读。"""
    index_path = experiment_index_path(workspace, slug, content_root=content_root)
    if not index_path.is_file():
        raise FileNotFoundError(f"experiment index not found: {index_path}")
    meta, body = parse_front_matter(index_path.read_text(encoding="utf-8"))
    current_phase = str(meta.get("phase") or "draft").lower()
    merged = {**meta, **{k: v for k, v in fields.items() if v is not None}}
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "phase" in fields and fields["phase"] is not None:
        target = str(fields["phase"]).lower()
        merged["phase"] = target
        if target != current_phase:
            validate_experiment_phase_transition(current_phase, target, mode=mode)
    validate_experiment_index_meta(merged)
    _atomic_write(index_path, _render_file(merged, body))
    written, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
    validate_experiment_index_meta(written)
    if "phase" in fields and fields["phase"] is not None and str(written.get("phase")).lower() != str(fields["phase"]).lower():
        _atomic_write(index_path, _render_file(meta, body))
        raise ExperimentIndexError("write-back re-read phase mismatch; restored previous index.md")
    return index_path


def commit_experiment_index_write(
    workspace: Path,
    slug: str,
    *,
    expected_from_phase: str,
    target_phase: str,
    current_plan_version: int | None = None,
    executor: str | None = None,
    topic: str | None = None,
    mode: str = "standard",
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """验证型写回：写前校验 expected_from + 允许边，写后回读；失败不留下目标 phase。"""
    index_path = experiment_index_path(workspace, slug, content_root=content_root)
    original = index_path.read_bytes() if index_path.is_file() else None
    try:
        validate_experiment_index_file(
            workspace,
            slug,
            content_root=content_root,
            expected_from_phase=expected_from_phase,
            target_phase=target_phase,
            mode=mode,
        )
        fields: dict[str, object] = {"phase": target_phase}
        if current_plan_version is not None:
            fields["current_plan_version"] = current_plan_version
        if executor is not None:
            fields["executor"] = executor
        if topic is not None:
            fields["topic"] = topic
        return update_experiment_index(
            workspace, slug, content_root=content_root, mode=mode, **fields
        )
    except Exception:
        if original is None:
            if index_path.is_file():
                index_path.unlink()
        else:
            index_path.write_bytes(original)
        raise


def write_experiment_review_yaml(
    workspace: Path,
    slug: str,
    filename: str,
    payload: dict[str, Any],
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """最小 reviews/ 落盘（M1 目录约定；完整 item 状态机归 M2）。"""
    slug = _require_slug(slug)
    if not filename.endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"
    reviews_dir = workspace / content_root / "experiments" / slug / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    path = reviews_dir / filename
    text = (
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
        + "\n"
    )
    _atomic_write(path, text)
    return path


def update_topic_index(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
    **fields: object,
) -> Path:
    """合并式更新 index.md 的 front-matter 字段（正文保持不变）。

    常用于验证型写：advance-round 改 round、close 改 status/close_reason。
    """
    topic_dir = workspace / content_root / "topics" / slug
    index_path = topic_dir / "index.md"
    if not index_path.is_file():
        raise FileNotFoundError(f"topic index not found: {index_path}")
    meta, body = parse_front_matter(index_path.read_text(encoding="utf-8"))
    meta.update({k: v for k, v in fields.items() if v is not None})
    _atomic_write(index_path, _render_file(meta, body))
    return index_path
