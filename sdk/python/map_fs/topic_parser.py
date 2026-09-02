"""读取：实时解析 map/ 平面（每次全量扫描，无缓存）。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from map_fs.action_items import _ACTION_ITEMS_FILE, parse_action_items_file
from map_fs.frontmatter import (
    _coerce_participants,
    _mtime_utc,
    _parse_dt,
    make_excerpt,
    parse_front_matter,
)
from map_fs.model import (
    DEFAULT_CONTENT_ROOT,
    FsAnomaly,
    FsComment,
    FsExperiment,
    FsPlane,
    FsTopic,
    FsWorkItem,
    comment_id_for_path,
    experiment_id_for_slug,
    parse_round_filename,
    topic_id_for_slug,
)


def _ack_error_of(meta: dict[str, Any], persona: str, round_number: int) -> str | None:
    """按 D2 三条判定 round 文件 ack 合规，返回失败原因（合规返回 None）。

    判定基于 RAW front-matter 字段 + 文件名 persona/轮次，**不用**解析层的
    fallback 值——否则无 frontmatter 的手写文件在 fallback 后"看起来合规"，
    ack 判定就被绕过了（review fa3b838b 核证的关键陷阱）。
    """
    raw_author = meta.get("author")
    if raw_author is None:
        return "frontmatter author missing"
    if str(raw_author) != persona:
        return f"frontmatter author={raw_author}, expected {persona}"
    raw_round = meta.get("round")
    if raw_round is None:
        return "frontmatter round missing"
    try:
        round_match = int(raw_round)
    except (TypeError, ValueError):
        return f"frontmatter round={raw_round!r}, expected {round_number}"
    if round_match != round_number:
        return f"round={round_match}, expected {round_number}"
    raw_posted = meta.get("posted_at")
    if raw_posted is None or _parse_dt(raw_posted) is None:
        return "posted_at missing or unparseable"
    return None


def _anomaly_level_of(meta: dict[str, Any], ack_error: str) -> str:
    """close_note D4 分级：字段存在但非法=invalid；缺失（含整块缺失）=lite。

    基于 ``_ack_error_of`` 的同一判定结果细化级别——不引入第二套判定规则，
    仅把合并文案（posted_at missing or unparseable）按 RAW 字段拆开定级。
    """
    if ack_error == "posted_at missing or unparseable":
        return "lite" if meta.get("posted_at") is None else "invalid"
    if "missing" in ack_error:  # frontmatter author/round missing
        return "lite"
    return "invalid"


def _ack_reason_of(topic: FsTopic, round_number: int, persona: str) -> str | None:
    """该 persona 本轮文件的不合规原因；无文件或合规时返回 None。

    供 round_ack_pending detail 逐条指认 `round1-participant.md: 原因`。
    """
    for c in topic.comments:
        if c.round == round_number and c.file_persona == persona:
            return c.ack_error
    return None


def _missing_detail(topic: FsTopic, round_number: int, persona: str) -> str:
    """missing persona 的展示文案：合规失败带 文件名: 原因，缺文件只显名字。"""
    reason = _ack_reason_of(topic, round_number, persona)
    if reason:
        return f"{_comment_name_of(topic, round_number, persona)}: {reason}"
    return persona


def _comment_name_of(topic: FsTopic, round_number: int, persona: str) -> str:
    for c in topic.comments:
        if c.round == round_number and c.file_persona == persona:
            return Path(c.file_path).name
    return f"round{round_number}-{persona}.md"


def parse_topic_dir(topic_dir: Path, workspace: Path) -> FsTopic | None:
    """解析单个话题文件夹；目录不存在返回 None。"""
    if not topic_dir.is_dir():
        return None
    slug = topic_dir.name
    index_path = topic_dir / "index.md"

    meta: dict[str, Any] = {}
    body = ""
    if index_path.is_file():
        meta, body = parse_front_matter(index_path.read_text(encoding="utf-8"))

    title = str(meta.get("title") or slug.replace("-", " ").title())
    description = str(meta.get("description") or "") or body.strip()
    status = str(meta.get("status") or "open").lower()
    creator = str(meta.get("creator") or "host")
    created_at = _parse_dt(meta.get("created_at"))

    action_items, action_items_error = parse_action_items_file(
        topic_dir / _ACTION_ITEMS_FILE
    )

    comments: list[FsComment] = []
    anomalies: list[FsAnomaly] = []
    updated_at = created_at
    for entry in sorted(topic_dir.iterdir()):
        if not entry.is_file():
            continue
        round_parts = parse_round_filename(entry.name)
        if round_parts is None:
            continue  # index.md 及其他文件不作为评论
        file_round, file_persona, is_summary_path = round_parts
        rel_path = entry.relative_to(workspace).as_posix()
        text = entry.read_text(encoding="utf-8")
        c_meta, c_body = parse_front_matter(text)
        author = str(c_meta.get("author") or file_persona)
        kind = str(c_meta.get("kind") or "user")
        # 新格式由独立 Summary 文件名自证；旧格式仍读
        # round<N>-<persona>.md frontmatter 中的 is_round_summary。
        is_summary = is_summary_path or bool(c_meta.get("is_round_summary", False))
        posted_at = _parse_dt(c_meta.get("posted_at")) or _mtime_utc(entry)
        ack_error = _ack_error_of(c_meta, file_persona, file_round)
        if ack_error is not None:
            anomalies.append(
                FsAnomaly(
                    file=rel_path,
                    reason=ack_error,
                    level=_anomaly_level_of(c_meta, ack_error),
                )
            )
        comments.append(
            FsComment(
                id=comment_id_for_path(rel_path),
                topic_slug=slug,
                round=int(c_meta.get("round") or file_round),
                author=author,
                kind=kind if kind in {"user", "system"} else "user",
                is_round_summary=is_summary,
                excerpt=make_excerpt(c_body),
                content=c_body,
                file_path=rel_path,
                posted_at=posted_at,
                comment_seq=0,
                file_persona=file_persona,
                ack_valid=ack_error is None,
                ack_error=ack_error,
            )
        )
        if updated_at is None or posted_at > updated_at:
            updated_at = posted_at

    # 同轮 Summary 稳定排在普通发言之后，不受 persona 字典序影响。
    # 同轮 Summary 稳定排在普通发言之后；普通发言按 posted_at 排序——
    # 「最新发言者」按时间判定，host 同轮接棒也能清掉 unread_change，
    # file_path 仅作 tie-break（同刻写入的稳定序）。
    def _sort_key(c: FsComment) -> tuple:
        posted = c.posted_at
        if posted is not None and posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)  # 防御 naive 值混比
        return (c.round, c.is_round_summary, posted or datetime.min.replace(tzinfo=timezone.utc), c.file_path)

    comments.sort(key=_sort_key)
    for seq, comment in enumerate(comments, start=1):
        comment.comment_seq = seq

    max_round = max((c.round for c in comments), default=1)
    round_str = str(meta.get("round") or f"round{max_round}")
    if round_str.isdigit():
        round_str = f"round{round_str}"
    round_number = max_round if round_str == "ready" else _round_number_of(round_str, max_round)

    if updated_at is None and index_path.is_file():
        updated_at = _mtime_utc(index_path)

    return FsTopic(
        slug=slug,
        id=topic_id_for_slug(slug),
        title=title,
        description=description[:2000],
        status=status if status in {"open", "closed"} else "open",
        round=round_str,
        round_number=round_number,
        creator=creator,
        created_at=created_at,
        updated_at=updated_at,
        dir_path=topic_dir.relative_to(workspace).as_posix(),
        comments=comments,
        declared_participants=_coerce_participants(meta.get("participants")),
        action_items=action_items,
        action_items_error=action_items_error,
        anomalies=anomalies,
    )


def _round_number_of(round_str: str, fallback: int) -> int:
    match = re.match(r"^round(\d+)$", round_str)
    return int(match.group(1)) if match else fallback


def parse_experiment_dir(exp_dir: Path, workspace: Path) -> FsExperiment | None:
    if not exp_dir.is_dir():
        return None
    slug = exp_dir.name
    index_path = exp_dir / "index.md"
    meta: dict[str, Any] = {}
    if index_path.is_file():
        meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))

    def _rel(name: str) -> str | None:
        p = exp_dir / name
        return p.relative_to(workspace).as_posix() if p.is_file() else None

    projection_id = _parse_uuid(meta.get("projection_id"))
    version_raw = meta.get("current_plan_version", 1)
    try:
        plan_version = int(version_raw)
    except (TypeError, ValueError):
        plan_version = 1
    return FsExperiment(
        slug=slug,
        id=experiment_id_for_slug(slug),
        title=str(meta.get("title") or slug.replace("-", " ").title()),
        description=str(meta.get("description") or ""),
        phase=str(meta.get("phase") or "draft").lower(),
        creator=str(meta.get("creator") or "host"),
        created_at=_parse_dt(meta.get("created_at")),
        dir_path=exp_dir.relative_to(workspace).as_posix(),
        plan_path=_rel("plan.md"),
        log_path=_rel("log.md"),
        review_path=_rel("review.md"),
        current_plan_version=plan_version,
        executor=str(meta.get("executor") or ""),
        topic=str(meta.get("topic") or ""),
        updated_at=_parse_dt(meta.get("updated_at")),
        projection_id=projection_id,
    )


def _parse_uuid(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def scan_plane(workspace: Path, content_root: str = DEFAULT_CONTENT_ROOT) -> FsPlane:
    """实时解析整个内容平面（每次全量扫描，无缓存）。

    Perf note (v0.13 M59a / F4): full parse per request is a deliberate
    design choice — the FS plane is the source of truth and there is no
    cache to invalidate. Baseline recorded at
    ``tests/perf-baselines/fs-scan-plane-baseline.json`` (re-measure with
    ``pytest tests/test_fs_scan_plane_perf_baseline.py -m slow``).
    Optimization trigger — only consider mtime-incremental scanning when
    topics > 500 or a single scan p95 > 100 ms; re-run the baseline first
    and diff (counts travel with the file to distinguish scale-driven from
    code-driven drift). YAGNI until then.
    """
    root = workspace / content_root
    plane = FsPlane()
    topics_dir = root / "topics"
    if topics_dir.is_dir():
        for entry in sorted(topics_dir.iterdir()):
            topic = parse_topic_dir(entry, workspace)
            if topic is not None:
                plane.topics.append(topic)
    experiments_dir = root / "experiments"
    if experiments_dir.is_dir():
        for entry in sorted(experiments_dir.iterdir()):
            exp = parse_experiment_dir(entry, workspace)
            if exp is not None:
                plane.experiments.append(exp)
    return plane


# ---------------------------------------------------------------------------
# work 推导（纯函数，waker 可直接复用）
# ---------------------------------------------------------------------------


def derive_work(topic: FsTopic, persona: str) -> list[FsWorkItem]:
    """从单个话题推导 persona 的待办。

    规则（v2，参与人白名单 + 文件存在性）：

    - ``pending_topic_reply``：话题 open 且未 ready，**persona 在参与人白名单内
      且不是 creator**，本轮还没有自己的发言文件。creator 不需要
      为新轮次先写开场文件，其主持义务由 round_ack_pending 承载。白名单外
      persona（如 reviewer）不产生待办——需要其参与时在 front-matter
      ``participants:`` 声明，或其主动发言（发言即自动并入白名单）。
    - ``round_ack_pending``（仅 host 视角）：本轮还有 ack 名单内参与者没交
      合规文件。missing 范围 = ``ack_participants()``（creator ∪ declared，
      动态 speaker 不扩员，D3）；判定用 effective ack authors（只统计
      frontmatter 合规，D1/D2），未发言/不合规逐条带文件名与原因（A5）。
    """
    items: list[FsWorkItem] = []
    if topic.status != "open":
        return items
    current = topic.round_number
    authors = topic.authors_in_round(current)
    is_participant = persona in topic.participants
    if (
        is_participant
        and persona != topic.creator
        and persona not in authors
        and topic.round != "ready"
    ):
        items.append(
            FsWorkItem(
                kind="pending_topic_reply",
                topic_slug=topic.slug,
                title=topic.title,
                round=current,
                detail=f"round{current} 尚无 {persona} 的发言文件",
                suggested_command=f"map topic comment --topic {topic.slug} --file <md>",
            )
        )
    if persona == topic.creator:
        ack_list = topic.ack_participants()
        missing = [p for p in ack_list if p != persona and p not in authors]
        if missing and topic.round != "ready":
            detail = "round{current} 待发言: {names}".format(
                current=current,
                names=", ".join(_missing_detail(topic, current, p) for p in missing),
            )
            items.append(
                FsWorkItem(
                    kind="round_ack_pending",
                    topic_slug=topic.slug,
                    title=topic.title,
                    round=current,
                    detail=detail,
                    suggested_command=f"map topic advance-round --topic {topic.slug}",
                )
            )
    return items
