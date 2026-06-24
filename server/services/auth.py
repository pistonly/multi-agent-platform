import secrets

import bcrypt
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()


def verify_token(token: str, token_hash: str) -> bool:
    return bcrypt.checkpw(token.encode(), token_hash.encode())


def create_agent(db: Session, name: str, role: AgentRole = AgentRole.agent) -> tuple[Agent, str]:
    token = secrets.token_urlsafe(32)
    agent = Agent(name=name, api_token_hash=hash_token(token), role=role)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, token


def get_agent_by_token(db: Session, token: str) -> Agent | None:
    agents = db.query(Agent).all()
    for agent in agents:
        if verify_token(token, agent.api_token_hash):
            return agent
    return None
