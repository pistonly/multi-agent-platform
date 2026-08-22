"""Self-service bootstrap endpoint — create project + persona agents without admin token.

``POST /api/v1/bootstrap`` is the zero-config onboarding path: a new user
calls it with just a project_key/name, and gets back the project plus the
plaintext API tokens for host/participant/reviewer (shown once).

This bypasses the admin-gated ``POST /projects`` + ``POST /agents`` flow so
users don't need to manually create an admin token first. Abuse is bounded
by ``project_key`` uniqueness (409 on collision) — each bootstrap owns its
own namespace.

``POST /api/v1/bootstrap/reissue`` (v0.11 M52C) is the self-service token
recovery path: reissue one agent's token by project_key + agent_name,
revoking the previous token immediately.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole, Project
from server.domain.schemas import (
    BootstrapAgentResult,
    BootstrapRequest,
    BootstrapResponse,
    ProjectRead,
    TokenReissueRequest,
    TokenReissueResponse,
)
from server.services import bootstrap_service
from server.services.auth import reissue_agent_token as _reissue_agent_token

bootstrap_router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


@bootstrap_router.post(
    "",
    response_model=BootstrapResponse,
    status_code=status.HTTP_201_CREATED,
)
def bootstrap(
    payload: BootstrapRequest,
    db: Session = Depends(get_db),
) -> BootstrapResponse:
    project, created_agents = bootstrap_service.run_bootstrap(
        db,
        project_key=payload.project_key,
        project_name=payload.project_name,
        workspace_path=payload.workspace_path,
        description=payload.description,
        content_root=payload.content_root,
        fs_freshness_sla_seconds=payload.fs_freshness_sla_seconds,
    )
    return BootstrapResponse(
        project=ProjectRead.model_validate(project),
        agents=[
            BootstrapAgentResult(
                persona=persona_key,
                agent_id=agent.id,
                agent_name=agent.name,
                api_token=token,
            )
            for persona_key, agent, token in created_agents
        ],
    )


@bootstrap_router.post(
    "/reissue",
    response_model=TokenReissueResponse,
    status_code=status.HTTP_200_OK,
)
def reissue_token(
    payload: TokenReissueRequest,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> TokenReissueResponse:
    """Reissue one agent's API token (self-service recovery, M52C).

    Requires a valid Bearer token. The caller must be an admin, or an
    agent belonging to the target project — knowing only the (public, committed)
    ``project_key`` is not enough to take over a persona's token. The
    previous token is revoked atomically (hash replaced in the same commit);
    callers should write the new token back to ``.map/agents.local.yaml``
    (``map auth reissue`` does this).
    """
    if agent.role != AgentRole.admin:
        project = db.scalar(select(Project).where(Project.project_key == payload.project_key))
        if project is not None and agent.project_id != project.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Not authorized to reissue tokens for this project",
            )

    agent, token = _reissue_agent_token(
        db,
        project_key=payload.project_key,
        agent_name=payload.agent_name,
    )
    return TokenReissueResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        project_key=payload.project_key,
        api_token=token,
        previous_token_revoked=True,
        reissued_at=datetime.now(timezone.utc),
    )
