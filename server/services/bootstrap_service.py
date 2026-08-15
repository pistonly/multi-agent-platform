"""Self-service bootstrap: atomically create a project + 3 persona agents.

This is the server-side backing for ``POST /api/v1/bootstrap``. It lets a
new user create a project and the 3 default persona agents
(host/participant/reviewer) in a single call **without an admin token**.

The entire operation runs in one DB transaction: if any step fails the
whole thing rolls back, so we never leak a half-created project or
orphan agents.
"""

from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Project
from server.services import auth as auth_service
from server.services import project_status_service as status_doc_service
from server.services.errors import ConflictError

# Mirror of ``map_client.bootstrap.DEFAULT_PERSONAS`` — the 3 personas every
# new project gets. Kept here so the server is the single source of truth
# for what a bootstrap creates, independent of which client calls it.
DEFAULT_PERSONAS: dict[str, dict[str, str]] = {
    "host": {
        "agent_name_suffix": "host",
        "description": "主持话题、从话题开实验、推进实验生命周期",
    },
    "participant": {
        "agent_name_suffix": "participant",
        "description": "参与话题讨论、提交 review、查看实验结果",
    },
    "reviewer": {
        "agent_name_suffix": "reviewer",
        "description": "评审实验计划、写 reasonable/unreasonable 项",
    },
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def run_bootstrap(
    db: Session,
    *,
    project_key: str,
    project_name: str,
    workspace_path: str,
    description: str | None = None,
) -> tuple[Project, list[tuple[str, Agent, str]]]:
    """Atomically create a project + 3 persona agents.

    Returns ``(project, [(persona_key, agent, plaintext_token), ...])``.
    Raises ``ConflictError`` if ``project_key`` or any agent name already
    exists — the caller gets a 409 and nothing is persisted.
    """
    # 1. project_key 全局唯一
    existing_project = db.scalar(
        select(Project).where(Project.project_key == project_key)
    )
    if existing_project is not None:
        raise ConflictError(
            "project_key already exists; bootstrap cannot recover tokens "
            "of an existing project. Pick a new project_key, or recover the "
            f"agents with `map auth reissue --key {project_key} --name <agent-name>`."
        )

    slug = _slug(project_key)

    # 2. 提前检查 agent name 唯一，避免 flush 时 IntegrityError
    #    （IntegrityError 会污染 session，需要 rollback 才能继续）
    persona_specs: list[tuple[str, str]] = [
        (pk, f"{slug}-{spec['agent_name_suffix']}")
        for pk, spec in DEFAULT_PERSONAS.items()
    ]
    for _persona_key, agent_name in persona_specs:
        if db.scalar(select(Agent).where(Agent.name == agent_name)) is not None:
            raise ConflictError(
                f"Agent name '{agent_name}' already exists; bootstrap cannot "
                "recover its token. Pick a new project_key, or recover it with "
                f"`map auth reissue --key {project_key} --name {agent_name}`."
            )

    # 3. 创建 project（flush 拿 id，不 commit）
    project = Project(
        project_key=project_key,
        name=project_name,
        workspace_path=workspace_path,
        description=description,
    )
    db.add(project)
    db.flush()

    # 4. 创建 3 个 persona agent（flush 拿 id，不 commit）
    created: list[tuple[str, Agent, str]] = []
    first_agent_id: uuid.UUID | None = None
    for persona_key, agent_name in persona_specs:
        token = secrets.token_urlsafe(32)
        agent = Agent(
            name=agent_name,
            api_token_hash=auth_service.hash_token(token),
            api_token_prefix=auth_service.token_prefix(token),
            role=AgentRole.agent,
            project_id=project.id,
        )
        db.add(agent)
        db.flush()
        if first_agent_id is None:
            first_agent_id = agent.id
        created.append((persona_key, agent, token))

    # 5. 创建 initial status version（用第一个 agent 作为 author）
    assert first_agent_id is not None
    status_doc_service.create_initial_status(
        db, project=project, author_agent_id=first_agent_id
    )

    # 6. 统一 commit —— 全部成功才持久化
    db.commit()
    db.refresh(project)
    for _pk, agent, _token in created:
        db.refresh(agent)

    return project, created
