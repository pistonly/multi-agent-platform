"""Self-service bootstrap endpoint — create project + persona agents without admin token.

``POST /api/v1/bootstrap`` is the zero-config onboarding path: a new user
calls it with just a project_key/name, and gets back the project plus the
plaintext API tokens for host/participant/reviewer (shown once).

This bypasses the admin-gated ``POST /projects`` + ``POST /agents`` flow so
users don't need to manually create an admin token first. Abuse is bounded
by ``project_key`` uniqueness (409 on collision) — each bootstrap owns its
own namespace.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from server.db.session import get_db
from server.domain.schemas import (
    BootstrapAgentResult,
    BootstrapRequest,
    BootstrapResponse,
    ProjectRead,
)
from server.services import bootstrap_service

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
