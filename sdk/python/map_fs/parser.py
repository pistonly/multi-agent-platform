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
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_CONTENT_ROOT = "map"

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs")

# round<N>-<persona>.md；persona 允许字母数字-_.
_ROUND_FILE_RE = re.compile(r"^round(\d+)-([A-Za-z0-9_.\-]+)\.md$")

# 话题执行项载体：action-items.yaml（列表文档，无 front-matter 围栏）
_ACTION_ITEMS_FILE = "action-items.yaml"
_ACTION_ITEM_STATUSES = {"open", "done", "cancelled"}

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
    # ack 合规标记（D2 三条，按 RAW front-matter + 文件名判定，不受 fallback 影响）
    file_persona: str = ""  # 文件名的 <persona> 段（round<N>-<persona>.md）
    ack_valid: bool = True  # frontmatter author/round/posted_at 与文件名一致
    ack_error: str | None = None  # 不合规的具体原因（合规时为 None）


@dataclass
class FsActionItem:
    """一条执行项 = action-items.yaml 中的一项（收敛时落盘，close 门禁清零）。"""

    id: int
    title: str
    owner: str  # persona 短名（host/participant），投影义务精确路由到人
    status: str  # open | done | cancelled
    evidence: str = ""  # done 必填（commit/pytest/文件路径）；带上完成闭环证据
    reason: str = ""  # cancelled 必填（显式放弃理由，审计留痕）
    created_at: datetime | None = None


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
    # action-items.yaml 派生；action_items_error 非 None 表示文件存在但格式错漏
    # （A1）——调用方（server close 门禁 / CLI 写回）据此拒绝操作，不静默。
    action_items: list[FsActionItem] = field(default_factory=list)
    action_items_error: str | None = None

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
        """本轮 **effective ack authors**：只统计 frontmatter 合规的 comment。

        手写旁路 / 空壳 / 错位文件（``ack_valid=False``）不构成 ack，
        derive_work 与 server validate 两端同源消费本集合。
        """
        return {c.author for c in self.comments if c.round == round_number and c.ack_valid}

    def ack_participants(self) -> list[str]:
        """ack 满员名单：index.md 声明的参与者 + creator（不含动态 speaker）。

        名单外 persona 同名文件不进 ack（D3）——手写 reviewer 文件不会被
        视为待 ack 成员、不阻塞 advance，仅作 anomaly 报告。
        """
        seen: list[str] = [self.creator]
        seen.extend(p for p in self.declared_participants if p not in seen)
        return seen

    def stray_files_in_round(self, round_number: int) -> list[FsComment]:
        """本轮名单外（不在 ack_participants）的发言文件 → anomaly 报告源。"""
        ack_list = set(self.ack_participants())
        return [c for c in self.comments if c.round == round_number and c.author not in ack_list]


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


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
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


def _require_slug(slug: str) -> str:
    """空 slug 防护：部分 typer/click 版本组合（实测 0.16.1 + 8.4.x）不强制
    校验必填 CLI 选项，None 会一路穿透到这里的路径拼接，抛出难懂的
    ``TypeError: PosixPath / NoneType``。SDK 层提前给出可读错误。"""
    if not slug or not str(slug).strip():
        raise ValueError(f"slug must be a non-empty folder name, got: {slug!r}")
    return slug


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


# ---------------------------------------------------------------------------
# 读取：实时解析
# ---------------------------------------------------------------------------


def parse_action_items_file(path: Path) -> tuple[list[FsActionItem], str | None]:
    """解析话题文件夹的 action-items.yaml → (items, error)。

    文件缺失 → ``([], None)``。列表文档每项字段：id/title/owner/status
    (open|done|cancelled)/evidence/reason/created_at。格式错漏（非列表、
    缺必填、status 非法、id 重复）返回 ``([], 错误文案)``——不静默（A1）：
    server close 门禁与 CLI 写回都据此拒绝，并在 409 / 报错里给出可读引导。
    """
    if not path.is_file():
        return [], None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        return [], f"{path.name} 不是合法 YAML: {err}"
    if raw is None:
        return [], None  # 空文件视作空列表（无执行项）
    if not isinstance(raw, list):
        return [], f"{path.name} 应为列表文档(list)，实际是 {type(raw).__name__}"
    items: list[FsActionItem] = []
    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            return [], f"{path.name} 第 {idx} 项不是对象(dict)"
        title = entry.get("title")
        owner = entry.get("owner")
        status = entry.get("status")
        if not isinstance(title, str) or not title.strip():
            return [], f"{path.name} 第 {idx} 项 title 缺失或为空"
        if not isinstance(owner, str) or not owner.strip():
            return [], f"{path.name} 第 {idx} 项 owner 缺失或为空"
        if status not in _ACTION_ITEM_STATUSES:
            return [], f"{path.name} 第 {idx} 项 status={status!r} 非法(可选 open|done|cancelled)"
        raw_id = entry.get("id")
        # id 可省略：按文档顺序 1-based 兜底（手写友好）；显式 id 须正整数
        if raw_id is None:
            raw_id = idx
        if not isinstance(raw_id, int) or raw_id <= 0:
            return [], f"{path.name} 第 {idx} 项 id={raw_id!r} 非正整数"
        items.append(
            FsActionItem(
                id=raw_id,
                title=str(title).strip(),
                owner=str(owner).strip(),
                status=status,
                evidence=str(entry.get("evidence") or "").strip(),
                reason=str(entry.get("reason") or "").strip(),
                created_at=_parse_dt(entry.get("created_at")),
            )
        )
    ids = [it.id for it in items]
    if len(ids) != len(set(ids)):
        return [], f"{path.name} 存在重复 id: {sorted(set(x for x in ids if ids.count(x) > 1))}"
    return items, None


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
    updated_at = created_at
    for entry in sorted(topic_dir.iterdir()):
        if not entry.is_file():
            continue
        match = _ROUND_FILE_RE.match(entry.name)
        if match is None:
            continue  # index.md 及其他文件不作为评论
        file_round = int(match.group(1))
        file_persona = match.group(2)
        rel_path = entry.relative_to(workspace).as_posix()
        text = entry.read_text(encoding="utf-8")
        c_meta, c_body = parse_front_matter(text)
        author = str(c_meta.get("author") or file_persona)
        kind = str(c_meta.get("kind") or "user")
        is_summary = bool(c_meta.get("is_round_summary", False))
        posted_at = _parse_dt(c_meta.get("posted_at")) or _mtime_utc(entry)
        ack_error = _ack_error_of(c_meta, file_persona, file_round)
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
        action_items=action_items,
        action_items_error=action_items_error,
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
# 写入：纯文件操作（CLI 与服务端验证型写共用）
# ---------------------------------------------------------------------------


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
    slug = _require_slug(slug)
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
    old_meta: dict[str, Any] = {}
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


def read_action_items(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> tuple[list[FsActionItem], str | None]:
    """读话题文件夹的 action-items.yaml；缺失 → ([], None)。"""
    slug = _require_slug(slug)
    topic_dir = workspace / content_root / "topics" / slug
    return parse_action_items_file(topic_dir / _ACTION_ITEMS_FILE)


def write_action_items(
    workspace: Path,
    slug: str,
    items: list[FsActionItem],
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """原子写回话题文件夹的 action-items.yaml（全量覆盖，列表文档无围栏）。

    每次写回规范化落盘（显式 id / created_at），空列表也写空文件——文件
    存在即接管该话题执行项（close 门禁对"无文件"话题不设限）。
    """
    slug = _require_slug(slug)
    payload = [
        {
            "id": item.id,
            "title": item.title,
            "owner": item.owner,
            "status": item.status,
            "evidence": item.evidence or None,
            "reason": item.reason or None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in items
    ]
    text = (
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        + "\n"
    )
    topic_dir = workspace / content_root / "topics" / slug
    path = topic_dir / _ACTION_ITEMS_FILE
    _atomic_write(path, text)
    return path


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
                )
            )
    return items
