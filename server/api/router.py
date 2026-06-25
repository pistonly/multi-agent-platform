"""Aggregate API routers for backward compatibility."""

from server.api.agents import agents_router
from server.api.audit import audit_router
from server.api.experiments import experiments_router
from server.api.projects import router
from server.api.status import status_router
from server.api.topics import topics_router
from server.api.webhooks import webhooks_router

__all__ = [
    "router",
    "agents_router",
    "audit_router",
    "experiments_router",
    "status_router",
    "topics_router",
    "webhooks_router",
]
