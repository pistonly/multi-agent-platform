"""FS 事实源的数据结构与确定性身份派生（纯数据，零 IO）。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_CONTENT_ROOT = "map"

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs")

# round<N>-<persona>.md；persona 允许字母数字-_.
_ROUND_FILE_RE = re.compile(r"^round(\d+)-([A-Za-z0-9_.\-]+)\.md$")


# ---------------------------------------------------------------------------
# 身份派生
# ---------------------------------------------------------------------------


def topic_id_for_slug(slug: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"topic:{slug}")


def experiment_id_for_slug(slug: str) -> uuid.UUID:
    """Deterministic FS experiment id: uuid5(experiment:<slug>)."""
    return uuid.uuid5(_NS, f"experiment:{slug}")


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
class FsAnomaly:
    """读路径 anomaly 报告条目（fs-write-entry-validation R1）。

    frontmatter 不合规的 round 文件只报告：不修正、不阻断读、不改写文件
    （存量脏 fixture 保留历史）。级别按 close_note D4 分：
    ``invalid``=字段存在但非法；``lite``=缺失（含整块 front-matter 缺失）。
    """

    file: str  # 相对 workspace 的 posix 路径
    reason: str  # 复用 _ack_error_of 的判定文案（D2 三条，同一套规则）
    level: str  # invalid | lite


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
    # 读路径 anomaly（fs-write-entry-validation R1/R2）：frontmatter 不合规的
    # round 文件报告位。只报告不阻断读、不改写；消费方不感知则为空列表。
    anomalies: list[FsAnomaly] = field(default_factory=list)
    # 关联实验列表（实验 cli-fs-topic-lifecycle-invariants A4）：
    # 供 close 门禁校验关联实验 phase 是否 terminal。parse_topic_dir 默认
    # 不注入（构造时取空 list），调用方（CLI local plane _require_local_topic
    # 或 server fs_source_service）从 FsPlane.experiments 反查 ``experiment.topic``
    # == ``self.slug`` 后赋值；字段缺失或空 list 视为「无关联实验」放行。
    experiments: list[FsExperiment] = field(default_factory=list)

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


# M1 index.md 契约：不变量字段。评审条目走 reviews/*.yaml，不进 index。
EXPERIMENT_INDEX_REQUIRED = (
    "phase",
    "current_plan_version",
    "creator",
    "executor",
    "topic",
    "updated_at",
)
EXPERIMENT_PHASES = frozenset(
    {
        "draft",
        "review",
        "approved",
        "running",
        "pending_review",
        "result_review",
        "done",
        "cancelled",
    }
)
_EXPERIMENT_STANDARD_EDGES: dict[str, frozenset[str]] = {
    "draft": frozenset({"review", "cancelled"}),
    "review": frozenset({"approved", "draft", "cancelled"}),
    "approved": frozenset({"running", "cancelled"}),
    "running": frozenset({"result_review", "pending_review", "cancelled"}),
    "pending_review": frozenset({"running", "cancelled"}),
    "result_review": frozenset({"done", "running", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}
_EXPERIMENT_DIRECT_EDGES: dict[str, frozenset[str]] = {
    "draft": frozenset({"running", "cancelled"}),
    "running": frozenset({"done", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}


class ExperimentIndexError(ValueError):
    """index.md 契约 / 验证型写失败（非法手改 phase 或不完整字段）。"""


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
    current_plan_version: int = 1
    executor: str = ""
    topic: str = ""
    updated_at: datetime | None = None
    projection_id: uuid.UUID | None = None


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
