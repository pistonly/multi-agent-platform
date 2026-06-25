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
