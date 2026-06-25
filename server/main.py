import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api.router import (
    agents_router,
    audit_router,
    experiments_router,
    router as projects_router,
    status_router,
    topics_router,
    webhooks_router,
)
from server.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Multi-Agent Platform", version="0.1.0")
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
    app.include_router(status_router, prefix=prefix)
    app.include_router(topics_router, prefix=prefix)
    app.include_router(webhooks_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=get_settings().debug)


if __name__ == "__main__":
    run()
