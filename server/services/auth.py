import secrets
import uuid

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole

TOKEN_PREFIX_LEN = 8


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()


def verify_token(token: str, token_hash: str) -> bool:
    return bcrypt.checkpw(token.encode(), token_hash.encode())


def token_prefix(token: str) -> str:
    return token[:TOKEN_PREFIX_LEN]


def create_agent(
    db: Session,
    name: str,
    role: AgentRole = AgentRole.agent,
    *,
    project_id: uuid.UUID | None = None,
) -> tuple[Agent, str]:
    if role == AgentRole.agent and project_id is None:
        raise ValueError("project_id is required for role=agent")
    if role == AgentRole.admin:
        project_id = None
    token = secrets.token_urlsafe(32)
    agent = Agent(
        name=name,
        api_token_hash=hash_token(token),
        api_token_prefix=token_prefix(token),
        role=role,
        project_id=project_id,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, token


def get_agent_by_token(db: Session, token: str) -> Agent | None:
    if not token:
        return None

    if len(token) >= TOKEN_PREFIX_LEN:
        prefix = token_prefix(token)
        stmt = select(Agent).where(Agent.api_token_prefix == prefix)
        for agent in db.scalars(stmt):
            if verify_token(token, agent.api_token_hash):
                return agent

    # Legacy agents created before api_token_prefix migration (empty prefix).
    legacy_stmt = select(Agent).where(Agent.api_token_prefix == "")
    for agent in db.scalars(legacy_stmt):
        if verify_token(token, agent.api_token_hash):
            return agent
    return None


def reissue_agent_token(
    db: Session,
    *,
    project_key: str,
    agent_name: str,
) -> tuple[Agent, str]:
    """Reissue an agent's API token, revoking the previous one (M52C).

    Self-service recovery path for a lost ``.map/agents.local.yaml``:
    the ``project_key`` is the proof of project ownership (same trust
    boundary as ``POST /bootstrap``). The old token becomes invalid
    immediately because the stored hash is replaced atomically.

    Raises ``NotFoundError`` (mapped to 404) when the project key or the
    agent name within that project does not exist — the 404 deliberately
    does not reveal which part was wrong.
    """
    from sqlalchemy import select as _select

    from server.domain.models import Project
    from server.services.errors import NotFoundError

    project = db.scalar(_select(Project).where(Project.project_key == project_key))
    if project is None:
        raise NotFoundError(
            f"project_key '{project_key}' not found. "
            "Check .map/config.yaml project_key, or bootstrap with a new key."
        )

    agent = db.scalar(
        _select(Agent).where(Agent.name == agent_name, Agent.project_id == project.id)
    )
    if agent is None:
        raise NotFoundError(
            f"agent '{agent_name}' not found in project '{project_key}'. "
            "Check the agent_name in .map/agents.yaml "
            "(e.g. multi-agent-platform-host)."
        )

    token = secrets.token_urlsafe(32)
    agent.api_token_hash = hash_token(token)
    agent.api_token_prefix = token_prefix(token)
    db.commit()
    db.refresh(agent)
    return agent, token
