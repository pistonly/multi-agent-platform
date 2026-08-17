"""map/ 文件夹即事实源：纯函数解析器与写入器。

约定（零 API、零 DB，纯文件系统）::

    map/
      topics/<slug>/
        index.md                  # front-matter: title/status/round/creator/...
        round<N>-<persona>.md     # 每轮每人一个文件 = 一条评论
      experiments/<slug>/
        index.md                  # front-matter: title/phase/creator/...
        plan.md / log.md / review.md

核心性质：

- **实时解析**：每次调用重新扫描目录，无缓存、无状态，DB 不存内容。
- **确定性身份**：topic id / comment id 由 uuid5 从 slug / 相对路径派生，
  跨解析稳定，UI 与 API 可直接当作主键使用。
- **ack 即文件存在**：参与者在本轮有自己的 ``round<N>-<persona>.md``
  即视为已发言（ack），平台无需单独记录。
- **excerpt 自动生成**：取正文首个一级标题（或首个非空行），截断 200 字符。

front-matter 为 YAML（``---`` 围栏），缺失时按文件名/正文兜底推导。
本模块只依赖标准库 + pyyaml，供 server 与 cli 共同使用。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

DEFAULT_CONTENT_ROOT = "map"

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs")

# round<N>-<persona>.md；persona 允许字母数字-_.
_ROUND_FILE_RE = re.compile(r"^round(\d+)-([A-Za-z0-9_.\-]+)\.md$")

_EXCERPT_MAX = 200
_FM_FENCE = "---"


# ---------------------------------------------------------------------------
# 身份派生
# ---------------------------------------------------------------------------


def topic_id_for_slug(slug: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"topic:{slug}")


def comment_id_for_path(rel_path: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"comment:{rel_path}")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FsComment:
    """一条评论 = 一个 round<N>-<persona>.md 文件。"""

    id: uuid.UUID
    topic_slug: str
    round: int
    author: str
    kind: str  # user | system
    is_round_summary: bool
    excerpt: str
    content: str  # 完整正文（不含 front-matter）
    file_path: str  # 相对 workspace 的 posix 路径
    posted_at: datetime | None
    comment_seq: int


@dataclass
class FsTopic:
    """一个话题 = map/topics/<slug>/ 一个文件夹。"""

    slug: str
    id: uuid.UUID
    title: str
    description: str
    status: str  # open | closed
    round: str  # roundN | ready
    round_number: int  # ready 时为当前推进前的轮号
    creator: str
    created_at: datetime | None
    updated_at: datetime | None
    dir_path: str  # 相对 workspace 的 posix 路径
    comments: list[FsComment] = field(default_factory=list)
    declared_participants: list[str] = field(default_factory=list)

    @property
    def participants(self) -> list[str]:
        """话题参与人白名单：creator ∪ declared ∪ speakers（展示顺序稳定）。

        - creator：默认参与，始终排首位
        - declared：index.md front-matter ``participants:``（topic-create 显式声明）
        - speakers：所有发言过的 persona（事实参与，发言即加入，按首次发言序）
        待办推导（derive_work）只对白名单内 persona 生成 pending_topic_reply，
        白名单外（如 reviewer）不再收到无关话题的待办噪音。
        """
        seen: list[str] = [self.creator]
        seen.extend(p for p in self.declared_participants if p not in seen)
        for c in self.comments:
            if c.author not in seen:
                seen.append(c.author)
        return seen

    def authors_in_round(self, round_number: int) -> set[str]:
        return {c.author for c in self.comments if c.round == round_number}


@dataclass
class FsExperiment:
    """一个实验 = map/experiments/<slug>/ 一个文件夹（plan/log 内容主体）。"""

    slug: str
    id: uuid.UUID
    title: str
    description: str
    phase: str  # draft | review | approved | running | result_review | done | cancelled
    creator: str
    created_at: datetime | None
    dir_path: str
    plan_path: str | None = None
    log_path: str | None = None
    review_path: str | None = None


@dataclass
class FsPlane:
    """一次实时解析的完整结果。"""

    topics: list[FsTopic] = field(default_factory=list)
    experiments: list[FsExperiment] = field(default_factory=list)

    def topic_by_slug(self, slug: str) -> FsTopic | None:
        for t in self.topics:
            if t.slug == slug:
                return t
        return None


@dataclass
class FsWorkItem:
    """从文件推导出的协作待办（纯函数产物，waker / CLI 共用）。"""

    kind: str  # pending_topic_reply | round_ack_pending
    topic_slug: str
    title: str
    round: int
    detail: str


# ---------------------------------------------------------------------------
# front-matter / excerpt 基础设施
# ---------------------------------------------------------------------------


def parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 ``---`` 围栏的 YAML front-matter，返回 (meta, body)。

    无围栏或 YAML 解析失败时返回 ({}, 原文)，永不抛错——
    事实源是手写文件，容错优先。
    """
    if not text.startswith(_FM_FENCE):
        return {}, text
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        return {}, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FM_FENCE:
            fm_text = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            try:
                meta = yaml.safe_load(fm_text)
            except yaml.YAMLError:
                return {}, text
            return meta if isinstance(meta, dict) else {}, body
    return {}, text


def make_excerpt(body: str, limit: int = _EXCERPT_MAX) -> str:
    """首个一级标题（或首个非空行）作为摘要，截断到 limit。"""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            stripped = stripped[2:].strip()
        return stripped[:limit]
    return ""


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _coerce_participants(raw: object) -> list[str]:
    """front-matter ``participants`` 容错归一：list / 单字符串 → 去空去重列表。

    手写 YAML 常见 ``participants: host`` 单值写法按单元素处理；其余类型
    （数字、dict 等）视为未声明，返回空列表。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for item in raw:
        name = str(item).strip() if item is not None else ""
        if name and name not in seen:
            seen.append(name)
    return seen


def slugify(text: str) -> str:
    """标题 → 文件夹安全的 slug（小写、连字符、剔除其余）。"""
    slug = re.sub(r"[^a-zA-Z0-9\-_\u4e00-\u9fff]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug or f"topic-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 读取：实时解析
# ---------------------------------------------------------------------------


def parse_topic_dir(topic_dir: Path, workspace: Path) -> FsTopic | None:
    """解析单个话题文件夹；目录不存在返回 None。"""
    if not topic_dir.is_dir():
        return None
    slug = topic_dir.name
    index_path = topic_dir / "index.md"

    meta: dict = {}
    body = ""
    if index_path.is_file():
        meta, body = parse_front_matter(index_path.read_text(encoding="utf-8"))

    title = str(meta.get("title") or slug.replace("-", " ").title())
    description = str(meta.get("description") or "") or body.strip()
    status = str(meta.get("status") or "open").lower()
    creator = str(meta.get("creator") or "host")
    created_at = _parse_dt(meta.get("created_at"))

    comments: list[FsComment] = []
    updated_at = created_at
    for entry in sorted(topic_dir.iterdir()):
        if not entry.is_file():
            continue
        match = _ROUND_FILE_RE.match(entry.name)
        if match is None:
            continue  # index.md 及其他文件不作为评论
        round_number = int(match.group(1))
        persona = match.group(2)
        rel_path = entry.relative_to(workspace).as_posix()
        text = entry.read_text(encoding="utf-8")
        c_meta, c_body = parse_front_matter(text)
        author = str(c_meta.get("author") or persona)
        kind = str(c_meta.get("kind") or "user")
        is_summary = bool(c_meta.get("is_round_summary", False))
        posted_at = _parse_dt(c_meta.get("posted_at")) or _mtime_utc(entry)
        comments.append(
            FsComment(
                id=comment_id_for_path(rel_path),
                topic_slug=slug,
                round=int(c_meta.get("round") or round_number),
                author=author,
                kind=kind if kind in {"user", "system"} else "user",
                is_round_summary=is_summary,
                excerpt=make_excerpt(c_body),
                content=c_body,
                file_path=rel_path,
                posted_at=posted_at,
                comment_seq=0,
            )
        )
        if updated_at is None or posted_at > updated_at:
            updated_at = posted_at

    comments.sort(key=lambda c: (c.round, c.file_path))
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
    )


def _round_number_of(round_str: str, fallback: int) -> int:
    match = re.match(r"^round(\d+)$", round_str)
    return int(match.group(1)) if match else fallback


def parse_experiment_dir(exp_dir: Path, workspace: Path) -> FsExperiment | None:
    if not exp_dir.is_dir():
        return None
    slug = exp_dir.name
    index_path = exp_dir / "index.md"
    meta: dict = {}
    if index_path.is_file():
        meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))

    def _rel(name: str) -> str | None:
        p = exp_dir / name
        return p.relative_to(workspace).as_posix() if p.is_file() else None

    return FsExperiment(
        slug=slug,
        id=uuid.uuid5(_NS, f"experiment:{slug}"),
        title=str(meta.get("title") or slug.replace("-", " ").title()),
        description=str(meta.get("description") or ""),
        phase=str(meta.get("phase") or "draft").lower(),
        creator=str(meta.get("creator") or "host"),
        created_at=_parse_dt(meta.get("created_at")),
        dir_path=exp_dir.relative_to(workspace).as_posix(),
        plan_path=_rel("plan.md"),
        log_path=_rel("log.md"),
        review_path=_rel("review.md"),
    )


def scan_plane(workspace: Path, content_root: str = DEFAULT_CONTENT_ROOT) -> FsPlane:
    """实时解析整个内容平面（每次全量扫描，无缓存）。

    Perf note (v0.13 M59a / F4): full parse per request is a deliberate
    design choice — the FS plane is the source of truth and there is no
    cache to invalidate. Baseline recorded at
    ``.map/perf-baselines/fs-scan-plane-baseline.json`` (re-measure with
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
# 写入：纯文件操作（CLI 与服务端验证型写共用）
# ---------------------------------------------------------------------------


def _render_file(meta: dict, body: str) -> str:
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
) -> Path:
    """创建（或覆盖）话题文件夹与 index.md。返回 index.md 路径。

    ``participants`` 为参与人白名单（declared），写入 front-matter；
    creator 始终隐含在内（不强制写入列表）。
    """
    topic_dir = workspace / content_root / "topics" / slug
    round_str = round_ if isinstance(round_, str) else f"round{round_}"
    meta: dict[str, object] = {
        "title": title,
        "status": status,
        "round": round_str,
        "creator": creator,
        "created_at": (datetime.now(timezone.utc).isoformat() if topic_dir.joinpath("index.md").exists() is False else None),
        "description": description or None,
    }
    old_meta: dict = {}
    if meta["created_at"] is None:  # 已存在 index：保留原 created_at
        old_meta, _ = parse_front_matter((topic_dir / "index.md").read_text(encoding="utf-8"))
        meta["created_at"] = old_meta.get("created_at")
    # participants：显式传入优先；未传且旧 index 已有 → 保留旧声明
    declared = _coerce_participants(participants)
    if not declared:
        declared = _coerce_participants(old_meta.get("participants"))
    if declared:
        meta["participants"] = declared
    body = f"# {title}\n\n{description}".rstrip() + "\n"
    index_path = topic_dir / "index.md"
    _atomic_write(index_path, _render_file(meta, body))
    return index_path


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
    发言人不在 index.md 的 participants 白名单时自动并入（发言即参与）；
    index.md 缺失（手建文件夹）时跳过并入，不影响评论写入。
    """
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


# ---------------------------------------------------------------------------
# work 推导（纯函数，waker 可直接复用）
# ---------------------------------------------------------------------------


def derive_work(topic: FsTopic, persona: str) -> list[FsWorkItem]:
    """从单个话题推导 persona 的待办。

    规则（v2，参与人白名单 + 文件存在性）：

    - ``pending_topic_reply``：话题 open 且未 ready，**persona 在参与人白名单内**
      （declared ∪ speakers ∪ creator），本轮还没有我的文件。白名单外
      persona（如 reviewer）不产生待办——需要其参与时在 front-matter
      ``participants:`` 声明，或其主动发言（发言即自动并入白名单）。
    - ``round_ack_pending``（仅 host 视角）：本轮还有白名单内参与者没交文件。
      missing 计算覆盖全量 participants，白名单过滤不削弱推进门禁。
    """
    items: list[FsWorkItem] = []
    if topic.status != "open":
        return items
    current = topic.round_number
    authors = topic.authors_in_round(current)
    is_participant = persona in topic.participants
    if is_participant and persona not in authors and topic.round != "ready":
        items.append(
            FsWorkItem(
                kind="pending_topic_reply",
                topic_slug=topic.slug,
                title=topic.title,
                round=current,
                detail=f"round{current} 尚无 {persona} 的发言文件",
            )
        )
    if persona == topic.creator:
        missing = [p for p in topic.participants if p != persona and p not in authors]
        if missing and topic.round != "ready":
            items.append(
                FsWorkItem(
                    kind="round_ack_pending",
                    topic_slug=topic.slug,
                    title=topic.title,
                    round=current,
                    detail=f"round{current} 待发言: {', '.join(missing)}",
                )
            )
    return items
