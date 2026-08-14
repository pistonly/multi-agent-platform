"""Aggregate API routers for backward compatibility."""

from server.api.action_items import action_items_router
from server.api.agents import agents_router
from server.api.audit import audit_router
from server.api.bootstrap import bootstrap_router
from server.api.docs import docs_router
from server.api.experiments import experiments_router
from server.api.feedback import feedback_router
from server.api.notifications import notifications_router
from server.api.projects import router
from server.api.status import status_router
from server.api.topics import topics_router
from server.api.webhooks import webhooks_router

__all__ = [
    "action_items_router",
    "bootstrap_router",
    "docs_router",
    "notifications_router",
    "router",
    "agents_router",
    "audit_router",
    "experiments_router",
    "feedback_router",
    "status_router",
    "topics_router",
    "webhooks_router",
]
