"""ASGI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Settings
from app.dependencies import check_dependencies

UI_DIR = Path(__file__).resolve().parent / "ui"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(check_dependencies, settings)
        yield

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=UI_DIR / "templates")
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

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

    return app
