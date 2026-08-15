"""map/ 文件夹事实源（fs plane）schema。

与 DB 版 schema 的区别：以 persona **名字**为身份（不依赖 agent 表），
id 由 uuid5 从 slug / 文件路径确定性派生，字段即文件约定本身。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FsCommentRead(BaseModel):
    """一条评论 = map/topics/<slug>/round<N>-<persona>.md。"""

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
    created_at: datetime | None = None
    updated_at: datetime | None = None
    dir_path: str


class FsTopicDetailRead(FsTopicSummaryRead):
    comments: list[FsCommentRead] = Field(default_factory=list)


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
    """验证型写：推进轮次（服务端校验 ack 后写回 index.md）。"""

    waive_ack: bool = False
    waive_reason: str | None = Field(default=None, max_length=1024)
    mark_ready: bool = False


class FsCloseRequest(BaseModel):
    """验证型写：关闭话题（写回 index.md 的 status/close_reason）。"""

    close_reason: str | None = Field(default=None, max_length=256)
    close_note: str | None = None


class FsWorkItemRead(BaseModel):
    """从文件推导的协作待办（waker / CLI 共用的纯函数产物）。"""

    kind: str
    topic_slug: str
    title: str
    round: int
    detail: str
