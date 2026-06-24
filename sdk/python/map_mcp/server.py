from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from map_client.client import MAPClient
from map_mcp._utils import dump, dumps_json, parse_uuid
from map_mcp.context import AgentContext
from server.domain.models import CommentAnchorType, ExperimentPhase, ReviewItemStatus
from server.domain.schemas import (
    CommentCreate,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    PlanInput,
    PlanRevise,
    ProjectStatusRevise,
    ReviewCreate,
)

_AGENT_INSTRUCTIONS = """\
Multi-Agent Platform (MAP) — experiment collaboration for project-bound AI agents.

Start with get_me to confirm your role and bound project_key, then read
map://project/{project_key}/current-status before creating or modifying experiments.

Typical workflow:
1. get_project_status (or current-status resource) for project context
2. create_experiment with a plan (optionally submit_for_review)
3. create_review with reasonable and unreasonable items
4. revise_plan or create_comment to address disputes; update_review_item to resolve items
5. approve_experiment → start_experiment → complete_experiment with execution log

Read-only experiment context: map://experiment/{experiment_id}
"""

_ADMIN_INSTRUCTIONS = """\
Multi-Agent Platform (MAP) — admin tools for projects and global oversight.

Use list_projects / get_global_status to discover context across projects.
Project Current Status MD is revised via revise_project_status (Admin only).

Typical workflow:
1. create_project or list_projects to manage workspaces
2. revise_project_status to publish Current Status for agents
3. create_experiment / review / plan / log tools work as in v0.1

Resources: map://project/{project_key}/current-status, map://experiment/{experiment_id}
"""


def build_server(
    client: MAPClient,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/mcp",
) -> FastMCP:
    """Build a FastMCP server wired to the given MAPClient."""
    ctx = AgentContext.from_client(client)
    instructions = _ADMIN_INSTRUCTIONS if ctx.is_admin else _AGENT_INSTRUCTIONS

    transport_security: TransportSecuritySettings | None = None
    if host in ("127.0.0.1", "localhost", "::1"):
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )

    mcp = FastMCP(
        "Multi-Agent Platform",
        instructions=instructions,
        host=host,
        port=port,
        streamable_http_path=path,
        transport_security=transport_security,
    )

    @mcp.custom_route("/health", methods=["GET"], name="health")
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "map-mcp", "role": ctx.role.value})

    @mcp.tool()
    def get_me() -> dict[str, Any]:
        """Return the authenticated agent profile (role, project_id, project_key)."""
        return dump(client.get_me())

    @mcp.tool()
    def get_project_status(project_id: str | None = None) -> dict[str, Any]:
        """Get project status snapshot including experiments and current status_md."""
        pid = ctx.resolve_project_id(project_id)
        return dump(client.get_project_status(pid))

    @mcp.tool()
    def list_project_status_versions(project_id: str | None = None) -> list[dict[str, Any]]:
        """List Current Status markdown revisions for a project (newest first)."""
        pid = ctx.resolve_project_id(project_id)
        return dump(client.list_project_status_versions(pid))

    @mcp.tool()
    def get_project_status_version(project_id: str | None, version: int) -> dict[str, Any]:
        """Get a specific Current Status markdown revision by version number."""
        pid = ctx.resolve_project_id(project_id)
        return dump(client.get_project_status_version(pid, version))

    @mcp.tool()
    def list_experiments(project_id: str | None = None, phase: str | None = None) -> list[dict[str, Any]]:
        """List experiments in a project, optionally filtered by phase."""
        pid = ctx.resolve_project_id(project_id)
        phase_enum = ExperimentPhase(phase) if phase else None
        return dump(client.list_experiments(pid, phase=phase_enum))

    @mcp.tool()
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        """Get experiment details including current plan summary."""
        return dump(client.get_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def create_experiment(
        title: str,
        plan_content_md: str,
        project_id: str | None = None,
        description: str | None = None,
        submit_for_review: bool = False,
    ) -> dict[str, Any]:
        """Create an experiment topic with an initial plan in the bound or specified project."""
        pid = ctx.resolve_project_id(project_id)
        payload = ExperimentCreate(
            title=title,
            description=description,
            plan=PlanInput(content_md=plan_content_md),
            submit_for_review=submit_for_review,
        )
        return dump(client.create_experiment(pid, payload))

    @mcp.tool()
    def submit_for_review(experiment_id: str) -> dict[str, Any]:
        """Move experiment from draft to review phase."""
        return dump(client.submit_for_review(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def approve_experiment(experiment_id: str) -> dict[str, Any]:
        """Approve experiment when all unreasonable review items are resolved."""
        return dump(client.approve_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def withdraw_from_review(experiment_id: str) -> dict[str, Any]:
        """Withdraw experiment from review back to draft."""
        return dump(client.withdraw_from_review(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def cancel_experiment(experiment_id: str) -> dict[str, Any]:
        """Cancel an experiment."""
        return dump(client.cancel_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def start_experiment(experiment_id: str) -> dict[str, Any]:
        """Start an approved experiment (approved → running)."""
        return dump(client.start_experiment(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def complete_experiment(
        experiment_id: str,
        summary: str,
        content_md: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Complete a running experiment and attach an execution log."""
        payload = ExperimentComplete(summary=summary, content_md=content_md, metadata=metadata)
        return dump(client.complete_experiment(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_plans(experiment_id: str) -> list[dict[str, Any]]:
        """List all plan versions for an experiment."""
        return dump(client.list_plans(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def get_plan(experiment_id: str, version: int) -> dict[str, Any]:
        """Get a specific plan version by number."""
        return dump(client.get_plan(parse_uuid(experiment_id, "experiment_id"), version))

    @mcp.tool()
    def revise_plan(
        experiment_id: str,
        content_md: str,
        change_note: str | None = None,
        addressed_item_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new plan revision; optionally link addressed unreasonable review items."""
        item_ids = [parse_uuid(item_id, "addressed_item_id") for item_id in (addressed_item_ids or [])]
        payload = PlanRevise(content_md=content_md, change_note=change_note, addressed_item_ids=item_ids)
        return dump(client.revise_plan(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def create_review(
        experiment_id: str,
        reasonable_items: list[str] | None = None,
        unreasonable_items: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit a structured review with reasonable and unreasonable items."""
        payload = ReviewCreate(
            reasonable_items=reasonable_items or [],
            unreasonable_items=unreasonable_items or [],
        )
        return dump(client.create_review(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_reviews(experiment_id: str) -> list[dict[str, Any]]:
        """List all reviews on an experiment."""
        return dump(client.list_reviews(parse_uuid(experiment_id, "experiment_id")))

    @mcp.tool()
    def update_review_item(item_id: str, status: str) -> dict[str, Any]:
        """Update a review item status (open, addressed, rebutted, resolved, withdrawn, escalated)."""
        return dump(
            client.update_review_item(
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
    ) -> dict[str, Any]:
        """Create a comment on a plan, review, review item, or another comment."""
        payload = CommentCreate(
            anchor_type=CommentAnchorType(anchor_type),
            anchor_id=parse_uuid(anchor_id, "anchor_id"),
            parent_id=parse_uuid(parent_id, "parent_id") if parent_id else None,
            body=body,
        )
        return dump(client.create_comment(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_comments(experiment_id: str, tree: bool = False) -> list[dict[str, Any]]:
        """List comments on an experiment; set tree=true for nested structure."""
        return dump(client.list_comments(parse_uuid(experiment_id, "experiment_id"), tree=tree))

    @mcp.tool()
    def create_log(
        experiment_id: str,
        summary: str,
        content_md: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an execution log entry to an experiment."""
        payload = ExperimentLogCreate(summary=summary, content_md=content_md, metadata=metadata)
        return dump(client.create_log(parse_uuid(experiment_id, "experiment_id"), payload))

    @mcp.tool()
    def list_logs(experiment_id: str) -> list[dict[str, Any]]:
        """List execution logs for an experiment."""
        return dump(client.list_logs(parse_uuid(experiment_id, "experiment_id")))

    if ctx.is_admin:

        @mcp.tool()
        def get_global_status(project_id: str | None = None) -> dict[str, Any]:
            """Return the global status board, optionally filtered by project UUID."""
            pid = parse_uuid(project_id, "project_id") if project_id else None
            return dump(client.get_global_status(pid))

        @mcp.tool()
        def list_projects(include_archived: bool = False) -> list[dict[str, Any]]:
            """List all projects (Admin only)."""
            return dump(client.list_projects(include_archived=include_archived))

        @mcp.tool()
        def get_project(project_id: str) -> dict[str, Any]:
            """Get a single project by UUID (Admin only)."""
            return dump(client.get_project(parse_uuid(project_id, "project_id")))

        @mcp.tool()
        def revise_project_status(
            project_id: str,
            content_md: str,
            change_note: str | None = None,
        ) -> dict[str, Any]:
            """Revise the project Current Status markdown document (Admin only)."""
            payload = ProjectStatusRevise(content_md=content_md, change_note=change_note)
            return dump(client.revise_project_status(parse_uuid(project_id, "project_id"), payload))

        @mcp.tool()
        def create_project(
            name: str,
            workspace_path: str,
            description: str | None = None,
            project_key: str | None = None,
        ) -> dict[str, Any]:
            """Create a new project bound to a workspace path (Admin only)."""
            key = project_key or name.lower().replace(" ", "-")
            return dump(client.create_project(key, name, workspace_path, description))

        @mcp.resource("map://project/{project_id}/status")
        def project_status_resource(project_id: str) -> str:
            """Read-only project status board snapshot (UUID form, Admin)."""
            return dumps_json(client.get_project_status(parse_uuid(project_id, "project_id")))

    @mcp.resource("map://experiment/{experiment_id}")
    def experiment_context(experiment_id: str) -> str:
        """Read-only snapshot: experiment detail, current plan, reviews, open disputes, recent comments."""
        eid = parse_uuid(experiment_id, "experiment_id")
        detail = client.get_experiment(eid)
        plans = client.list_plans(eid)
        reviews = client.list_reviews(eid)
        comments = client.list_comments(eid, tree=True)
        logs = client.list_logs(eid)

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
        """Read-only project Current Status: snapshot + status_md (prefer before starting work)."""
        project = client.get_project_by_key(project_key)
        return dumps_json(client.get_project_status(project.id))

    return mcp
