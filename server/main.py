from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.api.router import (
    action_items_router,
    agents_router,
    audit_router,
    experiments_router,
    feedback_router,
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
from server.services.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    StateTransitionError,
    UnauthorizedError,
)


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
        StateTransitionError: status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            return JSONResponse(status_code=code, content=content)

        return handler

    for exc_type, code in mapping.items():
        app.add_exception_handler(exc_type, make_handler(code))


def create_app(*, init_db_on_startup: bool = True) -> FastAPI:
    """Build the ASGI app.

    Args:
        init_db_on_startup: When ``True`` (default), the app runs
            :func:`server.db.session.init_db` inside a FastAPI lifespan
            handler so ``uvicorn server.main:app`` self-bootstraps the
            schema. Tests pass ``False`` and inject a per-test session via
            ``app.dependency_overrides``.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if init_db_on_startup:
            init_db()
        yield

    app = FastAPI(
        title="Multi-Agent Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = settings.api_prefix
    app.include_router(projects_router, prefix=prefix)
    app.include_router(experiments_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(status_router, prefix=prefix)
    app.include_router(topics_router, prefix=prefix)
    app.include_router(webhooks_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(feedback_router, prefix=prefix)
    app.include_router(action_items_router, prefix=prefix)

    register_domain_exception_handlers(app)

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
