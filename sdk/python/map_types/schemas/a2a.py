"""A2A 互操作投影 schema（M53，实验性）。

Agent Card 与 Task 投影均为只读导出：内容复用 persona 注册信息与
``server/domain/a2a_mapping.py`` 常量，不新建存储。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentCardSkillRead(BaseModel):
    """卡片 skills 条目：id + name 最小对（评审建议 2）。"""

    id: str
    name: str


class AgentCardRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: uuid.UUID = Field(serialization_alias="id")
    name: str
    description: str
    url: str
    skills: list[AgentCardSkillRead] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    protocol: str
    project_id: uuid.UUID | None = None


class AgentCardListRead(BaseModel):
    items: list[AgentCardRead] = Field(default_factory=list)
    total: int = 0
    protocol: str


class A2ATaskRead(BaseModel):
    """A2A Task 投影条目：MAP 话题轮次 / 实验生命周期 → Task。"""

    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    kind: str  # "topic" | "experiment"
    name: str
    status: str  # A2A task state（见 a2a_mapping.EXPERIMENT_TASK_STATE）
    map_ref: str  # 源对象引用，如 "topic:<slug>#round2" / "experiment:<id>"
    updated_at: datetime | None = None


class A2ATaskListRead(BaseModel):
    tasks: list[A2ATaskRead] = Field(default_factory=list)
    total: int = 0
    protocol: str
