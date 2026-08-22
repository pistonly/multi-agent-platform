from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.__version__ import __version__
from server.api.router import (
    a2a_router,
    action_items_router,
    agents_router,
    audit_router,
    bootstrap_router,
    docs_router,
    experiments_router,
    feedback_router,
    fs_router,
    notifications_router,
    status_router,
    topics_router,
    webhooks_router,
)
from server.api.router import (
    router as projects_router,
)
from server.config import get_settings
from server.db.session import init_db
from server.domain.state_machine import StateMachineError
from server.services.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    StateTransitionError,
    UnauthorizedError,
)
from server.spa import mount_spa, resolve_web_dist


def register_domain_exception_handlers(app: FastAPI) -> None:
    """集中把领域异常映射为 HTTP 响应。

    取代过去在每个端点里手写的 ``try: ... except (...) as exc: raise http_error(exc)``
    样板——端点现在只需让领域异常自然抛出，由本处理器统一翻译。
    输出与原 ``http_error`` 完全一致：``{"detail": str(exc)}`` + 对应状态码。
    """

    mapping = {
        NotFoundError: status.HTTP_404_NOT_FOUND,
        UnauthorizedError: status.HTTP_401_UNAUTHORIZED,
        ForbiddenError: status.HTTP_403_FORBIDDEN,
        ConflictError: status.HTTP_409_CONFLICT,
        BadRequestError: status.HTTP_400_BAD_REQUEST,
        StateTransitionError: status.HTTP_422_UNPROCESSABLE_ENTITY,
        # M56：裸 StateMachineError（domain 层 validate_phase_transition 抛出，
        # cancel / submit-review / approve / start 等直接调用）此前无映射 → 500；
        # 与 StateTransitionError 同样按 422 状态机拒绝处理（无 error_code 装饰）。
        StateMachineError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    }

    def make_handler(code: int):
        def handler(_request: Request, exc: Exception) -> JSONResponse:
            content: dict[str, str] = {"detail": str(exc)}
            reason = getattr(exc, "reason", None)
            if isinstance(reason, str) and reason:
                content["reason"] = reason
            actor_id = getattr(exc, "actor_id", None)
            if isinstance(actor_id, str) and actor_id:
                content["actor_id"] = actor_id
            experiment_id = getattr(exc, "experiment_id", None)
            if isinstance(experiment_id, str) and experiment_id:
                content["experiment_id"] = experiment_id
            # State-machine errors may carry a stable subcode + remediation
            # hint. Surface them so CLI / SDK callers can route on the
            # failure mode instead of pattern-matching the human-readable
            # ``detail`` string. Defaults are skipped so the response stays
            # compact for ordinary state-machine refusals.
            if isinstance(exc, StateTransitionError):
                error_code = getattr(exc, "error_code", None)
                if isinstance(error_code, str) and error_code:
                    content["error_code"] = error_code
                hint = getattr(exc, "hint", None)
                if isinstance(hint, str) and hint:
                    content["hint"] = hint
                retryable = getattr(exc, "retryable", None)
                if isinstance(retryable, bool):
                    content["retryable"] = retryable
            return JSONResponse(status_code=code, content=content)

        return handler

    for exc_type, code in mapping.items():
        app.add_exception_handler(exc_type, make_handler(code))


def register_request_validation_handler(app: FastAPI) -> None:
    """v0.12 M55A (E3/E4): make FastAPI 422s actionable.

    Keeps the default ``detail`` array byte-compatible (SDK/CLI consumers
    parse it) and appends the same envelope fields a ``StateTransitionError``
    surfaces — ``error_code`` / ``hint`` / ``retryable`` — so clients route
    on one shape. The hint names the missing fields and, for endpoints in
    ``_ENDPOINT_PAYLOAD_EXAMPLES``, embeds a minimal working payload the
    agent can copy. Other endpoints degrade to the field list (review r2
    scope agreement).
    """
    # v0.12 M55A: routes like POST /api/v1/projects/{uuid}/experiments
    # carry a path variable, so keys are matched by (method, path suffix).
    _ENDPOINT_PAYLOAD_EXAMPLES: dict[tuple[str, str], str] = {
        ("POST", "/experiments"): (
            '{"title": "M55-demo", "plan": {"content_md": "---\\ntitle: \\"实验标题\\"\\n'
            'acceptance:\\n  - \\"...\\"\\nevidence_keys:\\n  - \\"pytest_summary\\"\\n'
            'dependencies: []\\n---\\n..."}, "submit_for_review": true}'
        ),
    }

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        missing = sorted(
            {
                ".".join(str(part) for part in err.get("loc", ())[1:])
                for err in errors
                if err.get("type") in ("missing", "missing_argument")
            }
        )
        hint_parts: list[str] = []
        if missing:
            hint_parts.append(f"missing fields: {', '.join(missing)}")
        example = next(
            (
                ex
                for (method, suffix), ex in _ENDPOINT_PAYLOAD_EXAMPLES.items()
                if request.method == method and request.url.path.endswith(suffix)
            ),
            None,
        )
        if example is not None:
            hint_parts.append(
                f"minimal payload for {request.method} {request.url.path}: {example}"
            )
        content: dict[str, object] = {
            "detail": jsonable_encoder(errors),
            "error_code": "request_validation_error",
            "retryable": False,
        }
        if hint_parts:
            content["hint"] = "; ".join(hint_parts)
        return JSONResponse(status_code=422, content=content)


def create_app(
    *,
    init_db_on_startup: bool = True,
    serve_web: bool | None = None,
    web_dist: str | Path | None = None,
) -> FastAPI:
    """Build the ASGI app.

    Args:
        init_db_on_startup: When ``True`` (default), the app runs
            :func:`server.db.session.init_db` inside a FastAPI lifespan
            handler so ``uvicorn server.main:app`` self-bootstraps the
            schema. Tests pass ``False`` and inject a per-test session via
            ``app.dependency_overrides``.
        serve_web: Override ``MAP_SERVE_WEB``. ``None`` uses settings.
        web_dist: Override ``MAP_WEB_DIST`` / packaged ``server/web_dist``.
    """
    settings = get_settings()
    enable_web = settings.serve_web if serve_web is None else serve_web
    dist = resolve_web_dist(web_dist if web_dist is not None else settings.web_dist)
    if enable_web and dist is None:
        # 静默降级排查成本高（看板无声消失、只剩 JSON 404）——必须留痕。
        import logging

        logging.getLogger(__name__).warning(
            "MAP_SERVE_WEB is enabled but no usable web dist was found "
            "(MAP_WEB_DIST=%r); starting API-only without the bundled board. "
            "Run scripts/sync-web-dist.sh or install a wheel built with web_dist.",
            str(settings.web_dist),
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if init_db_on_startup:
            init_db()
        yield

    app = FastAPI(
        title="Multi-Agent Platform",
        version=__version__,
        lifespan=lifespan,
    )
    # SPA middleware must be registered before CORS so the index.html
    # fallback still gets Access-Control-* headers (last add_middleware
    # is outermost).
    if enable_web and dist is not None:
        mount_spa(app, dist)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = settings.api_prefix
    app.include_router(bootstrap_router, prefix=prefix)
    app.include_router(projects_router, prefix=prefix)
    app.include_router(experiments_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(status_router, prefix=prefix)
    app.include_router(topics_router, prefix=prefix)
    app.include_router(webhooks_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(feedback_router, prefix=prefix)
    app.include_router(fs_router, prefix=prefix)
    app.include_router(action_items_router, prefix=prefix)
    app.include_router(docs_router, prefix=prefix)
    app.include_router(a2a_router, prefix=prefix)

    register_domain_exception_handlers(app)
    register_request_validation_handler(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("server.main:app", host="0.0.0.0", port=settings.port, reload=settings.debug)


if __name__ == "__main__":
    run()
