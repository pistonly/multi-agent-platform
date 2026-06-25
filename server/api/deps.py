from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from server.db.session import get_db
from server.domain.models import Agent
from server.services.auth import get_agent_by_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_agent(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agent:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authorization")
    agent = get_agent_by_token(db, credentials.credentials)
    if agent is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    return agent


def get_optional_current_agent(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agent | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return get_agent_by_token(db, credentials.credentials)
