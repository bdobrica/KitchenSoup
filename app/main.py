"""ASGI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.api.artifacts import router as artifact_router
from app.config import Settings
from app.db.session import create_database_engine
from app.dependencies import check_dependencies
from app.services.artifacts import ArtifactService, UploadError
from app.storage.base import ObjectNotFound, ObjectTooLarge, StorageError
from app.storage.s3 import S3ArtifactStore

UI_DIR = Path(__file__).resolve().parent / "ui"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(
    settings: Settings | None = None, artifact_service: ArtifactService | None = None
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(check_dependencies, settings)
        engine = None
        store = None
        try:
            app.state.artifact_service = artifact_service
            if artifact_service is None and settings.storage_enabled:
                engine = create_database_engine(settings)
                store = S3ArtifactStore(settings)
                app.state.artifact_service = ArtifactService(
                    store,
                    sessionmaker(engine, expire_on_commit=False),
                    bucket=settings.s3_bucket,
                    max_bytes=settings.upload_max_bytes,
                    url_ttl=settings.storage_url_ttl,
                )
            yield
        finally:
            if store is not None:
                store.close()
            if engine is not None:
                engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=UI_DIR / "templates")
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
    app.include_router(artifact_router)

    @app.exception_handler(UploadError)
    async def upload_error(request: Request, error: UploadError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"detail": str(error)},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, error: StorageError) -> JSONResponse:
        status = (
            404
            if isinstance(error, ObjectNotFound)
            else 413
            if isinstance(error, ObjectTooLarge)
            else 503
        )
        return JSONResponse(
            status_code=status,
            content={"detail": str(error)},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Database unavailable; verify migrations and connectivity"},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """Report process liveness; no infrastructure dependencies are checked."""
        return HealthResponse()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"app_name": settings.app_name},
        )

    @app.get("/artifacts", response_class=HTMLResponse, include_in_schema=False)
    async def artifact_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="artifacts.html", context={"app_name": settings.app_name}
        )

    return app
