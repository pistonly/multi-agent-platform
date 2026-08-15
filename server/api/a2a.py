"""A2A 互操作只读投影端点（M53，实验性）。

- ``GET /projects/{project_id}/agent-cards`` — 项目全部 persona 的 Agent Card
- ``GET /agents/{agent_id}/agent-card`` — 单 agent 卡片
- ``GET /agents/{agent_id}/tasks`` — 该 agent 视角的 A2A Task 投影

内容复用 persona 注册信息与 ``server/domain/a2a_mapping.py`` 常量，
不新建存储。鉴权沿用项目内 Bearer token（评审建议 1：不开匿名读）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from map_types.schemas import (
    A2ATaskListRead,
    A2ATaskRead,
    AgentCardListRead,
    AgentCardRead,
    AgentCardSkillRead,
)
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.a2a_mapping import (
    A2A_PROTOCOL_BASELINE,
    EXPERIMENT_TASK_STATE,
    PERSONA_CARDS,
)
from server.domain.models import Agent, Project
from server.services import fs_source_service, project_service, topic_progress_service

a2a_router = APIRouter(tags=["a2a"])


def _card_url(agent: Agent) -> str:
    return f"/api/v1/agents/{agent.id}/agent-card"


def _agent_persona(agent: Agent) -> str | None:
    """从 ``<project_key>-<persona>`` 命名约定提取 persona。

    ``Agent.persona`` 仅识别 ``multi-agent-platform-*`` 前缀（capability
    系统约定），卡片投影面向任意 bootstrap 项目，故按命名约定自行提取。
    """
    if agent.name and "-" in agent.name:
        suffix = agent.name.rsplit("-", 1)[-1]
        if suffix in PERSONA_CARDS:
            return suffix
    return None


def _build_card(agent: Agent) -> AgentCardRead:
    persona = _agent_persona(agent)
    preset = PERSONA_CARDS.get(persona) if persona else None
    if preset is None:
        preset = {
            "description": "MAP project agent (no persona preset)",
            "skills": [],
            "capabilities": [],
        }
    return AgentCardRead(
        agent_id=agent.id,
        name=agent.name,
        description=preset["description"],
        url=_card_url(agent),
        skills=[AgentCardSkillRead(**s) for s in preset["skills"]],
        capabilities=list(preset["capabilities"]),
        protocol=A2A_PROTOCOL_BASELINE,
        project_id=agent.project_id,
    )


def _load_agent_in_scope(
    db: Session, agent_id: uuid.UUID, caller: Agent
) -> Agent:
    """404 unknown agent; 403 cross-project access."""
    target = db.get(Agent, agent_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    if caller.role.value != "admin" and target.project_id != caller.project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent belongs to another project",
        )
    return target


@a2a_router.get(
    "/projects/{project_id}/agent-cards",
    response_model=AgentCardListRead,
)
def list_project_agent_cards(
    project_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentCardListRead:
    if agent.role.value != "admin" and agent.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="project not accessible for this agent",
        )
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )
    members = (
        db.query(Agent).filter(Agent.project_id == project_id).order_by(Agent.name).all()
    )
    cards = [_build_card(m) for m in members]
    return AgentCardListRead(
        items=cards, total=len(cards), protocol=A2A_PROTOCOL_BASELINE
    )


@a2a_router.get(
    "/agents/{agent_id}/agent-card",
    response_model=AgentCardRead,
)
def get_agent_card(
    agent_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentCardRead:
    target = _load_agent_in_scope(db, agent_id, agent)
    return _build_card(target)


@a2a_router.get("/agents/{agent_id}/tasks", response_model=A2ATaskListRead)
def get_agent_tasks(
    agent_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> A2ATaskListRead:
    """A2A Task projection of the agent's work view.

    Topic rounds → Task（open 轮次 = working）；experiments → Task state
    （``a2a_mapping.EXPERIMENT_TASK_STATE``）。复用 topic_progress 与
    fs_source 投影，仅做状态字段映射，不复制业务逻辑。
    """
    target = _load_agent_in_scope(db, agent_id, agent)
    tasks: list[A2ATaskRead] = []

    if target.project_id is not None:
        progress = topic_progress_service.list_topic_progress_for_agent(db, target)
        for item in progress.items:
            tasks.append(
                A2ATaskRead(
                    id=item.topic_id,
                    kind="topic",
                    name=f"{item.topic_title} (round {item.discussion_round})",
                    status="working",
                    map_ref=f"topic:{item.topic_id}#round-{item.discussion_round}",
                )
            )
        fs_items = fs_source_service.fs_topic_progress_for_agent(db, target)
        seen = {t.id for t in tasks}
        for item in fs_items:
            if item.topic_id in seen:
                continue
            tasks.append(
                A2ATaskRead(
                    id=item.topic_id,
                    kind="topic",
                    name=f"{item.topic_title} (round {item.discussion_round})",
                    status="working",
                    map_ref=f"topic:{item.topic_id}#round-{item.discussion_round}",
                )
            )

        experiments, _total = project_service.list_experiments(
            db, target.project_id
        )
        for exp in experiments:
            state = EXPERIMENT_TASK_STATE[exp.phase]
            tasks.append(
                A2ATaskRead(
                    id=exp.id,
                    kind="experiment",
                    name=exp.title,
                    status=state,
                    map_ref=f"experiment:{exp.id}",
                    updated_at=exp.updated_at,
                )
            )

    return A2ATaskListRead(
        tasks=tasks, total=len(tasks), protocol=A2A_PROTOCOL_BASELINE
    )
