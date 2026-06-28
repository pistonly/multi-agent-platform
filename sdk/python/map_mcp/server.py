from __future__ import annotations

from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from map_client.client import MAPClient
from map_client.config import load_config
from map_mcp._utils import dump, dumps_json, parse_uuid
from map_mcp.auth import BearerTokenMiddleware
from map_mcp.session import ClientResolver, token_param
from map_types import (
    CommentAnchorType,
    CommentCreate,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    ExperimentPhase,
    PlanInput,
    PlanRevise,
    ProjectStatusRevise,
    ReviewCreate,
    ReviewItemStatus,
    TopicCommentCreate,
    TopicCreate,
    TopicStatus,
)

Token = Annotated[str | None, token_param()]

_INSTRUCTIONS = """\
Multi-Agent Platform (MAP) — experiment collaboration for AI agents.

Authentication (transport layer):
- HTTP: set Authorization: Bearer <MAP agent token> on the MCP connection (e.g. Cursor mcp.json headers).
- stdio: set MAP_TOKEN in the MCP server process environment.
- Use separate MCP entries (map-admin / map-agent) for admin vs project-bound agent roles.

Start with get_me to confirm role and bound project_key, then read project context via get_project_status.

Typical workflow:
1. get_project_status for project context
2. create_experiment with a plan (optionally submit_for_review; topic_id only if you are the topic host)
3. create_review with reasonable and unreasonable items (often a second MCP connection / reviewer token)
4. revise_plan or create_comment to address disputes; update_review_item to resolve items
5. approve_experiment → start_experiment → complete_experiment with execution log

Admin-only tools (create_project, list_projects, etc.) require an admin token on the connection.

Read-only experiment context: map://experiment/{experiment_id}
"""


def build_server(
    client: MAPClient | None = None,
    *,
    api_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/mcp",
) -> FastMCP:
    """Build a FastMCP server. HTTP auth via Authorization Bearer; stdio may use a default client."""
    resolved_api_url = api_url or (client.base_url if client is not None else load_config()["api_url"])
    resolved_transport = transport or (client._transport if client is not None else None)
    resolver = ClientResolver(resolved_api_url, client, transport=resolved_transport)

    transport_security: TransportSecuritySettings | None = None
    if host in ("127.0.0.1", "localhost", "::1"):
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )

    mcp = FastMCP(
        "Multi-Agent Platform",
        instructions=_INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path=path,
        transport_security=transport_security,
    )

    @mcp.custom_route("/health", methods=["GET"], name="health")
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "map-mcp", "auth": "bearer"})

    @mcp.tool()
    def get_todos(token: Token = None) -> dict[str, Any]:
        """Return the current agent's to-do list: pending reviews, pending replies, own open experiments and topics."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.get_todos())

    @mcp.tool()
    def list_notifications(
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        token: Token = None,
    ) -> dict[str, Any]:
        """List in-app notifications for the current agent (paginated, optional unread filter)."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_notifications(unread_only=unread_only, limit=limit, offset=offset))

    @mcp.tool()
    def mark_notification_read(notification_id: str, token: Token = None) -> dict[str, Any]:
        """Mark a single in-app notification as read."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.mark_notification_read(parse_uuid(notification_id, "notification_id")))

    @mcp.tool()
    def mark_all_notifications_read(token: Token = None) -> dict[str, Any]:
        """Mark all in-app notifications as read for the current agent."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.mark_all_notifications_read())

    @mcp.tool()
    def get_audit_history(target_type: str, target_id: str, token: Token = None) -> list[dict[str, Any]]:
        """Return the audit trail for a given object (e.g. target_type='experiment', 'topic')."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_audit_for_target(target_type, parse_uuid(target_id, "target_id")))

    @mcp.tool()
    def get_me(token: Token = None) -> dict[str, Any]:
        """Return the authenticated agent profile (role, project_id, project_key)."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.get_me())

    @mcp.tool()
    def get_project_status(project_id: str | None = None, token: Token = None) -> dict[str, Any]:
        """Get project status: structured snapshot (active_experiments, open_topics, experiment_counts_by_phase, etc.) plus narrative status_md. Prefer snapshot fields for experiment/topic lists; use status_md for goals, blockers, and next steps only."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            return dump(c.get_project_status(pid))

    @mcp.tool()
    def list_project_status_versions(project_id: str | None = None, token: Token = None) -> list[dict[str, Any]]:
        """List Current Status markdown revisions for a project (newest first)."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            return dump(c.list_project_status_versions(pid))

    @mcp.tool()
    def get_project_status_version(
        project_id: str | None,
        version: int,
        token: Token = None,
    ) -> dict[str, Any]:
        """Get a specific Current Status markdown revision by version number."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            return dump(c.get_project_status_version(pid, version))

    @mcp.tool()
    def list_experiments(
        project_id: str | None = None,
        phase: str | None = None,
        creator_agent_id: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
        token: Token = None,
    ) -> list[dict[str, Any]]:
        """List experiments in a project, with optional phase/search/pagination/archive filters."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            phase_enum = ExperimentPhase(phase) if phase else None
            creator_id = parse_uuid(creator_agent_id, "creator_agent_id") if creator_agent_id else None
            return dump(
                c.list_experiments(
                    pid,
                    phase=phase_enum,
                    creator_agent_id=creator_id,
                    q=q,
                    page=page,
                    page_size=page_size,
                    include_archived=include_archived,
                )
            )

    @mcp.tool()
    def list_topics(
        project_id: str | None = None,
        status: str | None = None,
        creator_agent_id: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
        token: Token = None,
    ) -> list[dict[str, Any]]:
        """List discussion topics in a project, with optional status/search/pagination/archive filters."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            st = TopicStatus(status) if status else None
            creator_id = parse_uuid(creator_agent_id, "creator_agent_id") if creator_agent_id else None
            return dump(
                c.list_topics(
                    pid,
                    status=st,
                    creator_agent_id=creator_id,
                    q=q,
                    page=page,
                    page_size=page_size,
                    include_archived=include_archived,
                )
            )

    @mcp.tool()
    def get_topic(topic_id: str, token: Token = None) -> dict[str, Any]:
        """Get a topic detail: discussion thread, linked experiments, and counts."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.get_topic(parse_uuid(topic_id, "topic_id")))

    @mcp.tool()
    def create_topic(
        title: str,
        project_id: str | None = None,
        description: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Create a lightweight discussion topic (no plan required) in the bound or specified project."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            payload = TopicCreate(title=title, description=description)
            return dump(c.create_topic(pid, payload))

    @mcp.tool()
    def create_topic_comment(
        topic_id: str,
        body: str,
        parent_id: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Add a comment to a topic discussion thread."""
        with resolver.use(token) as (c, _ctx):
            payload = TopicCommentCreate(
                body=body,
                parent_id=parse_uuid(parent_id, "parent_id") if parent_id else None,
            )
            return dump(c.create_topic_comment(parse_uuid(topic_id, "topic_id"), payload))

    @mcp.tool()
    def close_topic(topic_id: str, token: Token = None) -> dict[str, Any]:
        """Close a topic (open → closed)."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.close_topic(parse_uuid(topic_id, "topic_id")))

    @mcp.tool()
    def reopen_topic(topic_id: str, token: Token = None) -> dict[str, Any]:
        """Reopen a closed topic (closed → open)."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.reopen_topic(parse_uuid(topic_id, "topic_id")))

    @mcp.tool()
    def get_experiment(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Get experiment details including current plan summary."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.get_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def create_experiment(
        title: str,
        plan_content_md: str,
        project_id: str | None = None,
        description: str | None = None,
        submit_for_review: bool = False,
        topic_id: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Create an experiment with an initial plan. When topic_id is set, only the topic host (or admin) may call this."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            payload = ExperimentCreate(
                title=title,
                description=description,
                plan=PlanInput(content_md=plan_content_md),
                submit_for_review=submit_for_review,
                topic_id=parse_uuid(topic_id, "topic_id") if topic_id else None,
            )
            return dump(c.create_experiment(pid, payload))

    @mcp.tool()
    def submit_for_review(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Move experiment from draft to review phase."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.submit_for_review(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def approve_experiment(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Approve experiment when all unreasonable review items are resolved."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.approve_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def withdraw_from_review(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Withdraw experiment from review back to draft."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.withdraw_from_review(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def cancel_experiment(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Cancel an experiment."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.cancel_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def start_experiment(experiment_id: str, token: Token = None) -> dict[str, Any]:
        """Start an approved experiment (approved → running)."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.start_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def complete_experiment(
        experiment_id: str,
        summary: str,
        content_md: str,
        metadata: dict[str, Any] | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Complete a running experiment and attach an execution log."""
        with resolver.use(token) as (c, _ctx):
            payload = ExperimentComplete(summary=summary, content_md=content_md, metadata=metadata)
            return dump(c.complete_experiment(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_plans(experiment_id: str, token: Token = None) -> list[dict[str, Any]]:
        """List all plan versions for an experiment."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_plans(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def get_plan(experiment_id: str, version: int, token: Token = None) -> dict[str, Any]:
        """Get a specific plan version by number."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.get_plan(parse_uuid(experiment_id, "experiment_id"), version))

    @mcp.tool()
    def revise_plan(
        experiment_id: str,
        content_md: str,
        change_note: str | None = None,
        addressed_item_ids: list[str] | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Create a new plan revision; optionally link addressed unreasonable review items."""
        with resolver.use(token) as (c, _ctx):
            item_ids = [parse_uuid(item_id, "addressed_item_id") for item_id in (addressed_item_ids or [])]
            payload = PlanRevise(content_md=content_md, change_note=change_note, addressed_item_ids=item_ids)
            return dump(c.revise_plan(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def create_review(
        experiment_id: str,
        reasonable_items: list[str] | None = None,
        unreasonable_items: list[str] | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Submit a structured review with reasonable and unreasonable items."""
        with resolver.use(token) as (c, _ctx):
            payload = ReviewCreate(
                reasonable_items=reasonable_items or [],
                unreasonable_items=unreasonable_items or [],
            )
            return dump(c.create_review(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_reviews(experiment_id: str, token: Token = None) -> list[dict[str, Any]]:
        """List all reviews on an experiment."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_reviews(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def update_review_item(item_id: str, status: str, token: Token = None) -> dict[str, Any]:
        """Update a review item status (open, addressed, rebutted, resolved, withdrawn, escalated)."""
        with resolver.use(token) as (c, _ctx):
            return dump(
                c.update_review_item(
                    parse_uuid(item_id, "item_id"),
                    ReviewItemStatus(status),
                )
            )

    @mcp.tool()
    def create_comment(
        experiment_id: str,
        anchor_type: str,
        anchor_id: str,
        body: str,
        parent_id: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Create a comment on a plan, review, review item, or another comment."""
        with resolver.use(token) as (c, _ctx):
            payload = CommentCreate(
                anchor_type=CommentAnchorType(anchor_type),
                anchor_id=parse_uuid(anchor_id, "anchor_id"),
                parent_id=parse_uuid(parent_id, "parent_id") if parent_id else None,
                body=body,
            )
            return dump(c.create_comment(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_comments(experiment_id: str, tree: bool = False, token: Token = None) -> list[dict[str, Any]]:
        """List comments on an experiment; set tree=true for nested structure."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_comments(parse_uuid(experiment_id, "experiment_id"), tree=tree))

    @mcp.tool()
    def create_log(
        experiment_id: str,
        summary: str,
        content_md: str,
        metadata: dict[str, Any] | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Append an execution log entry to an experiment."""
        with resolver.use(token) as (c, _ctx):
            payload = ExperimentLogCreate(summary=summary, content_md=content_md, metadata=metadata)
            return dump(c.create_log(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_logs(experiment_id: str, token: Token = None) -> list[dict[str, Any]]:
        """List execution logs for an experiment."""
        with resolver.use(token) as (c, _ctx):
            return dump(c.list_logs(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def get_global_status(project_id: str | None = None, token: Token = None) -> dict[str, Any]:
        """Return the global status board, optionally filtered by project UUID (Admin only)."""
        with resolver.use(token) as (c, ctx):
            ctx.require_admin()
            pid = parse_uuid(project_id, "project_id") if project_id else None
            return dump(c.get_global_status(pid))

    @mcp.tool()
    def list_projects(include_archived: bool = False, token: Token = None) -> list[dict[str, Any]]:
        """List all projects (Admin only)."""
        with resolver.use(token) as (c, ctx):
            ctx.require_admin()
            return dump(c.list_projects(include_archived=include_archived))

    @mcp.tool()
    def get_project(project_id: str, token: Token = None) -> dict[str, Any]:
        """Get a single project by UUID (Admin only)."""
        with resolver.use(token) as (c, ctx):
            ctx.require_admin()
            return dump(c.get_project(parse_uuid(project_id, "project_id")))

    @mcp.tool()
    def revise_project_status(
        content_md: str,
        project_id: str | None = None,
        change_note: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Revise the project Current Status markdown (Admin or project-bound agent)."""
        with resolver.use(token) as (c, ctx):
            pid = ctx.resolve_project_id(project_id)
            payload = ProjectStatusRevise(content_md=content_md, change_note=change_note)
            return dump(c.revise_project_status(pid, payload))

    @mcp.tool()
    def create_project(
        name: str,
        workspace_path: str,
        description: str | None = None,
        project_key: str | None = None,
        token: Token = None,
    ) -> dict[str, Any]:
        """Create a new project bound to a workspace path (Admin only)."""
        with resolver.use(token) as (c, ctx):
            ctx.require_admin()
            key = project_key or name.lower().replace(" ", "-")
            return dump(c.create_project(key, name, workspace_path, description))

    @mcp.resource("map://project/{project_id}/status")
    def project_status_resource(project_id: str) -> str:
        """Read-only project status board snapshot (UUID form, Admin)."""
        with resolver.use(None) as (c, ctx):
            if not ctx.is_admin:
                raise ValueError("Admin role required for this resource")
            return dumps_json(c.get_project_status(parse_uuid(project_id, "project_id")))

    @mcp.resource("map://experiment/{experiment_id}")
    def experiment_context(experiment_id: str) -> str:
        """Read-only snapshot: experiment detail, plans, reviews, disputes, comments."""
        with resolver.use(None) as (c, _ctx):
            eid = parse_uuid(experiment_id, "experiment_id")
            detail = c.get_experiment(eid)
            plans = c.list_plans(eid)
            reviews = c.list_reviews(eid)
            comments = c.list_comments(eid, tree=True)
            logs = c.list_logs(eid)

            open_items: list[dict[str, Any]] = []
            for review in reviews:
                for item in review.items:
                    if item.kind.value == "unreasonable" and item.status and item.status.value == "open":
                        open_items.append(dump(item))

            context = {
                "experiment": dump(detail),
                "current_plan": dump(detail.current_plan),
                "plan_versions": dump(plans),
                "reviews": dump(reviews),
                "open_unreasonable_items": open_items,
                "comments_tree": dump(comments),
                "logs": dump(logs),
            }
            return dumps_json(context)

    @mcp.resource("map://project/{project_key}/current-status")
    def project_current_status_resource(project_key: str) -> str:
        """Read-only project Current Status."""
        with resolver.use(None) as (c, _ctx):
            project = c.get_project_by_key(project_key)
            return dumps_json(c.get_project_status(project.id))

    _original_streamable_http_app = mcp.streamable_http_app

    def streamable_http_app_with_bearer():
        app = _original_streamable_http_app()
        app.add_middleware(BearerTokenMiddleware)
        return app

    mcp.streamable_http_app = streamable_http_app_with_bearer  # type: ignore[method-assign]

    return mcp
