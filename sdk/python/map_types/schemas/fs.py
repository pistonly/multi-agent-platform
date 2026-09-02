"""map/ 文件夹事实源（fs plane）schema。

与 DB 版 schema 的区别：以 persona **名字**为身份（不依赖 agent 表），
id 由 uuid5 从 slug / 文件路径确定性派生，字段即文件约定本身。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .content_source import ContentSourceMeta
from .project import normalize_content_root


class FsCommentRead(BaseModel):
    """一条评论 = 普通 round 文件或独立 Round Summary 文件。"""

    id: uuid.UUID
    topic_slug: str
    round: int
    author: str
    kind: str = "user"
    is_round_summary: bool = False
    excerpt: str = ""
    content: str = ""
    file_path: str
    posted_at: datetime | None = None
    comment_seq: int
    # ack 合规标记（D2 三条，parser 判定后随投影上行；旧客户端缺失时
    # 默认按合规处理，避免旧投影不必要地阻断 advance）
    file_persona: str = ""
    ack_valid: bool = True
    ack_error: str | None = None


class FsTopicSummaryRead(BaseModel):
    """一个话题 = map/topics/<slug>/ 文件夹（index.md + 评论文件）。"""

    id: uuid.UUID
    slug: str
    title: str
    description: str = ""
    status: str = "open"
    discussion_round: str = "round1"
    creator: str
    comment_count: int = 0
    participants: list[str] = Field(default_factory=list)
    declared_participants: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    dir_path: str


class FsActionItemRead(BaseModel):
    """一条执行项 = action-items.yaml 中的一项（收敛时落盘，close 门禁清零）。"""

    id: int
    title: str
    owner: str  # persona 短名（host/participant），投影义务精确路由到人
    status: str = "open"  # open | done | cancelled
    evidence: str = ""  # done 必填（commit/pytest/文件路径）
    reason: str = ""  # cancelled 必填（显式放弃理由）
    created_at: datetime | None = None


class FsTopicDetailRead(FsTopicSummaryRead):
    comments: list[FsCommentRead] = Field(default_factory=list)
    action_items: list[FsActionItemRead] = Field(default_factory=list)
    action_items_error: str | None = None  # yaml 存在但格式错漏（A1，不静默）


class FsExperimentRead(BaseModel):
    """一个实验内容包 = map/experiments/<slug>/ 文件夹。"""

    id: uuid.UUID
    slug: str
    title: str
    description: str = ""
    phase: str = "draft"
    creator: str
    created_at: datetime | None = None
    dir_path: str
    plan_path: str | None = None
    log_path: str | None = None
    review_path: str | None = None


class FsAdvanceRoundRequest(BaseModel):
    """验证型写：推进轮次（服务端校验 ack 后写回 index.md）。

    远程/容器部署（server 看不到 workspace）时必须携带 ``base_revision``
    （CLI 先 ``map fs sync`` 投影再取 revision）；服务端只信任已 CAS 发布
    的投影来校验 ack 完整性。``evidence`` 为旧客户端兼容字段，不再参与
    权限或 ack 校验。
    """

    waive_ack: bool = False
    waive_reason: str | None = Field(default=None, max_length=1024)
    mark_ready: bool = False
    base_revision: int | None = Field(default=None, ge=1)
    evidence: FsTopicDetailRead | None = None


class FsCloseRequest(BaseModel):
    """验证型写：关闭话题（写回 index.md 的 status/close_reason）。"""

    close_reason: str | None = Field(default=None, max_length=256)
    close_note: str | None = None
    base_revision: int | None = Field(default=None, ge=1)
    evidence: FsTopicDetailRead | None = None


class FsWorkItemRead(BaseModel):
    """从文件推导的协作待办（waker / CLI 共用的纯函数产物）。"""

    kind: str
    topic_slug: str
    title: str
    round: int
    detail: str


# ---------------------------------------------------------------------------
# 部署矩阵显式化：workspace 可达性握手
# ---------------------------------------------------------------------------


class FsPlaneStatusRead(BaseModel):
    """server 视角的 FS plane 可达状态（部署矩阵探测握手）。

    - ``local-fs``：server 能直接读 ``<workspace>/<content_root>/``（同机部署
      或容器内同路径挂载），实时解析 + 服务端写回均可用。
    - ``projection-cache``：workspace 不可达，但存在 ``map fs push`` 上行的
      投影缓存——读路径回退到缓存，验证型写走 validate → 本地写回 → commit。
    - ``detached``：两者皆无，FS plane 对 server 不可见（读写链路均断，
      ``hint`` 给出修复指引）。
    """

    workspace_path: str
    content_root: str
    workspace_exists: bool
    content_root_exists: bool
    mode: str
    projection_pushed_at: datetime | None = None
    projection_revision: int | None = None
    publisher_agent_id: uuid.UUID | None = None
    consistency_model: str | None = None
    hint: str = ""
    source: ContentSourceMeta | None = None


class FsWriteVerdictRead(BaseModel):
    """验证型写（validate 阶段）判定结果。

    ``allowed=True`` 时 ``fields`` 是 CLI 应本地写回 index.md 的 front-matter
    字段；``token`` 是 server 签发的短时 HMAC 凭证，本地写回完成后凭它
    commit（审计 + 通知 + 投影缓存刷新）。防伪造 verdict，不防恶意客户端
    （本地文件主权本就在 Agent 侧）。
    """

    action: str  # advance-round | close
    allowed: bool = True
    slug: str
    fields: dict[str, str] = Field(default_factory=dict)
    token: str
    expires_at: datetime
    base_revision: int = 0
    topic: FsTopicSummaryRead


class FsWriteCommitRequest(BaseModel):
    """本地写回完成后的 commit 请求（凭 validate 签发的 token）。"""

    token: str
    slug: str
    action: str = Field(description="advance-round | close（与 validate 的 action 一致）")
    applied_fields: dict[str, str] = Field(
        default_factory=dict,
        description="客户端实际写回的字段（与 verdict.fields 比对，不一致时 409）",
    )


class FsWriteCommitResponse(BaseModel):
    accepted: bool = True
    action: str
    slug: str
    projection_revision: int | None = None


class FsProjectionPushRequest(BaseModel):
    """``map fs push`` 上行的 FS plane 投影（远程/容器部署的读侧回退源）。

    内容主权仍在本地文件：这里只是 server 侧的只读投影缓存，push 幂等
    覆盖。``topics`` 携带评论元数据与正文（供 Web UI / work 投影离线渲染）。
    """

    client_workspace: str = Field(description="推送端本地 workspace 绝对路径（审计用）")
    content_root: str | None = Field(
        default=None,
        description="Must match Project.content_root when set; omitted keeps P0 clients working",
    )
    base_revision: int | None = Field(
        default=None,
        ge=1,
        description="CAS 基线；首次 push 为空，已有投影时必须等于当前 revision",
    )
    content_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="topics/experiments 规范化内容的 SHA-256；server 会复核",
    )
    topics: list[FsTopicDetailRead] = Field(default_factory=list)
    experiments: list[FsExperimentRead] = Field(default_factory=list)
    pushed_at: datetime | None = Field(
        default=None, description="缺省由 server 盖当前时间戳"
    )

    @field_validator("content_root")
    @classmethod
    def validate_content_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_content_root(value)


class FsProjectionMetaRead(BaseModel):
    """投影缓存元信息（不含正文，供 status / UI 展示）。"""

    pushed_at: datetime
    pushed_by_agent_id: uuid.UUID | None = None
    publisher_agent_id: uuid.UUID | None = None
    owner_agent_id: uuid.UUID | None = None
    client_workspace: str
    topic_count: int
    experiment_count: int
    projection_revision: int = 1
    content_hash: str | None = None
    consistency_model: str = "single-publisher-eventual"
    content_root: str | None = None


class FsProjectionObjectHash(BaseModel):
    kind: str  # topic | experiment
    slug: str
    content_hash: str


class FsProjectionInventoryRead(BaseModel):
    """Object-level hash list for CLI diff without downloading bodies."""

    projection_revision: int
    content_hash: str
    content_root: str
    publisher_agent_id: uuid.UUID | None = None
    pushed_at: datetime
    objects: list[FsProjectionObjectHash] = Field(default_factory=list)
    source: ContentSourceMeta | None = None


class FsProjectionChange(BaseModel):
    kind: str  # topic_upsert | topic_delete | experiment_upsert | experiment_delete
    slug: str
    value: Any = None
    expected_hash: str | None = None

    @model_validator(mode="after")
    def parse_value(self) -> FsProjectionChange:
        if self.kind == "topic_upsert":
            if self.value is None:
                raise ValueError("topic_upsert requires value")
            if not isinstance(self.value, FsTopicDetailRead):
                self.value = FsTopicDetailRead.model_validate(self.value)
        elif self.kind == "experiment_upsert":
            if self.value is None:
                raise ValueError("experiment_upsert requires value")
            if not isinstance(self.value, FsExperimentRead):
                self.value = FsExperimentRead.model_validate(self.value)
        elif self.kind in {"topic_delete", "experiment_delete"}:
            self.value = None
            if not self.expected_hash:
                raise ValueError(f"{self.kind} requires expected_hash")
        else:
            raise ValueError(f"unknown delta change kind: {self.kind}")
        return self


class FsProjectionDeltaRequest(BaseModel):
    """Incremental CAS update. Deletes must be explicit tombstones."""

    base_revision: int = Field(ge=1)
    client_workspace: str
    content_root: str
    changes: list[FsProjectionChange] = Field(default_factory=list)
    result_content_hash: str = Field(min_length=64, max_length=64)

    @field_validator("content_root")
    @classmethod
    def validate_content_root(cls, value: str) -> str:
        return normalize_content_root(value)


class FsProjectionDeltaResult(FsProjectionMetaRead):
    applied_changes: int = 0
    tombstones: int = 0
    noop: bool = False


def _canonical_topic_dict(topic: FsTopicDetailRead) -> dict[str, Any]:
    data = topic.model_dump(
        mode="json",
        exclude={
            "created_at",
            "updated_at",
            "dir_path",
            "comments",
            "action_items",
            "action_items_error",
        },
    )
    data["comments"] = [
        comment.model_dump(mode="json", exclude={"posted_at", "file_path"})
        for comment in sorted(
            topic.comments, key=lambda item: (item.round, item.author, item.comment_seq)
        )
    ]
    data["action_items"] = [
        item.model_dump(mode="json", exclude={"created_at"})
        for item in sorted(topic.action_items, key=lambda item: item.id)
    ]
    data["action_items_error"] = topic.action_items_error
    return data


def _canonical_experiment_dict(experiment: FsExperimentRead) -> dict[str, Any]:
    return experiment.model_dump(mode="json", exclude={"created_at", "dir_path"})


def _sha256_canonical(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def fs_topic_content_hash(topic: FsTopicDetailRead) -> str:
    return _sha256_canonical(_canonical_topic_dict(topic))


def fs_experiment_content_hash(experiment: FsExperimentRead) -> str:
    return _sha256_canonical(_canonical_experiment_dict(experiment))


def fs_projection_content_hash(
    topics: list[FsTopicDetailRead], experiments: list[FsExperimentRead]
) -> str:
    """生成跨客户端稳定的投影内容摘要，不包含路径、时间戳或 CAS 元数据。"""

    topic_payloads = [
        _canonical_topic_dict(topic) for topic in sorted(topics, key=lambda item: item.slug)
    ]
    experiment_payloads = [
        _canonical_experiment_dict(item)
        for item in sorted(experiments, key=lambda item: item.slug)
    ]
    return _sha256_canonical({"topics": topic_payloads, "experiments": experiment_payloads})
