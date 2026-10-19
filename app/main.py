"""ASGI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response

from app.api.artifacts import router as artifact_router
from app.api.conversations import router as conversation_router
from app.api.datasets import router as dataset_router
from app.api.deployments import router as deployment_router
from app.api.documents import router as document_router
from app.api.evaluations import router as evaluation_router
from app.api.models import router as model_router
from app.api.providers import router as provider_router
from app.api.runs import router as run_router
from app.api.targets import router as target_router
from app.api.training import router as training_router
from app.api.transformations import router as transformation_router
from app.api.versions import router as version_router
from app.config import Settings
from app.db.session import create_database_engine
from app.dependencies import check_dependencies
from app.deployments.local import LocalDockerDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.errors import explain
from app.evaluation.local import LocalInferenceRunner
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.executors.materialize import RegisteredInputMaterializer
from app.ingestion.sources import SourceError
from app.jobs.queue import JobQueue
from app.providers.credentials import LocalCredentialResolver
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.schemas import ProviderError
from app.registry.huggingface import HuggingFaceResolver
from app.registry.inspection import RegistryError
from app.services.artifacts import ArtifactService, UploadError
from app.services.datasets import DatasetService
from app.services.deployments import DeploymentService
from app.services.evaluations import EvaluationService
from app.services.models import ModelService
from app.services.providers import ProviderService
from app.services.runs import TrainingRunService
from app.services.targets import TargetService
from app.services.training import TrainingPlanService
from app.services.transformations import TransformationService
from app.storage.base import ObjectNotFound, ObjectTooLarge, StorageError
from app.storage.s3 import S3ArtifactStore
from app.transformations.local import LocalTransformExecutor

UI_DIR = Path(__file__).resolve().parent / "ui"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(
    settings: Settings | None = None,
    artifact_service: ArtifactService | None = None,
    model_service: ModelService | None = None,
    dataset_service: DatasetService | None = None,
    provider_service: ProviderService | None = None,
    training_plan_service: TrainingPlanService | None = None,
    training_run_service: TrainingRunService | None = None,
    evaluation_service: EvaluationService | None = None,
    deployment_service: DeploymentService | None = None,
    transformation_service: TransformationService | None = None,
    target_service: TargetService | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(check_dependencies, settings)
        engine = None
        store = None
        queue = None
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
            app.state.model_service = model_service
            if model_service is None and app.state.artifact_service is not None:
                artifacts = app.state.artifact_service
                app.state.model_service = ModelService(
                    artifacts.factory, artifacts, HuggingFaceResolver()
                )
            app.state.dataset_service = dataset_service
            if dataset_service is None and app.state.artifact_service is not None:
                artifacts = app.state.artifact_service
                app.state.dataset_service = DatasetService(artifacts.factory, artifacts)
            app.state.provider_service = provider_service
            if provider_service is None:
                if engine is None and settings.check_dependencies:
                    engine = create_database_engine(settings)
                factory = (
                    sessionmaker(engine, expire_on_commit=False)
                    if engine is not None
                    else app.state.artifact_service.factory
                    if app.state.artifact_service is not None
                    else None
                )
                if factory is not None:
                    credentials = LocalCredentialResolver(
                        {
                            name.strip()
                            for name in settings.provider_env_names.split(",")
                            if name.strip()
                        },
                        Path(settings.provider_secret_directory),
                    )
                    app.state.provider_service = ProviderService(
                        factory, lambda config: OpenAICompatibleProvider(config, credentials)
                    )
            app.state.training_plan_service = training_plan_service
            if training_plan_service is None and app.state.dataset_service is not None:
                app.state.training_plan_service = TrainingPlanService(
                    app.state.dataset_service.factory
                )
            app.state.target_service = target_service
            if target_service is None and app.state.artifact_service is not None:
                app.state.target_service = TargetService(app.state.artifact_service, settings)
            app.state.training_run_service = training_run_service
            if training_run_service is None and (
                settings.local_training_enabled
                or settings.ssh_execution_enabled
                or settings.kubernetes_execution_enabled
                or settings.sagemaker_execution_enabled
            ):
                if app.state.artifact_service is None or app.state.training_plan_service is None:
                    raise ValueError("Training requires artifact storage and plan metadata")
                executor = (
                    LocalDockerTrainingExecutor(
                        Path(settings.training_workspace),
                        RegisteredInputMaterializer(app.state.artifact_service),
                        image=settings.training_image,
                        memory_gib=settings.training_memory_gib,
                        cpus=settings.training_cpus,
                        timeout=settings.training_timeout,
                    )
                    if settings.local_training_enabled
                    else None
                )
                queue = JobQueue(settings.valkey_url)
                app.state.training_run_service = TrainingRunService(
                    app.state.training_plan_service,
                    executor,
                    queue,
                    app.state.artifact_service,
                    app.state.target_service,
                )
            app.state.evaluation_service = evaluation_service
            if evaluation_service is None and app.state.artifact_service is not None:
                runs = app.state.training_run_service
                runner = (
                    LocalInferenceRunner(
                        runs.local_executor, RegisteredInputMaterializer(app.state.artifact_service)
                    )
                    if runs is not None
                    and isinstance(runs.local_executor, LocalDockerTrainingExecutor)
                    else None
                )
                app.state.evaluation_service = EvaluationService(app.state.artifact_service, runner)
            app.state.deployment_service = deployment_service
            if deployment_service is None and app.state.artifact_service is not None:
                artifacts = app.state.artifact_service
                deployment_executor = (
                    LocalDockerDeploymentExecutor(
                        Path(settings.deployment_workspace),
                        DeploymentMaterializer(RegisteredInputMaterializer(artifacts)),
                        image=settings.deployment_image,
                    )
                    if settings.local_deployment_enabled
                    else None
                )
                app.state.deployment_service = DeploymentService(
                    artifacts, deployment_executor, app.state.target_service
                )
            app.state.transformation_service = transformation_service
            if transformation_service is None and app.state.artifact_service is not None:
                artifacts = app.state.artifact_service
                app.state.transformation_service = TransformationService(
                    artifacts,
                    LocalTransformExecutor(
                        Path(settings.transformation_workspace),
                        DeploymentMaterializer(RegisteredInputMaterializer(artifacts)),
                        settings.transformation_image,
                    )
                    if settings.local_transformations_enabled
                    else None,
                )
            yield
        finally:
            if queue is not None:
                queue.close()
            if store is not None:
                store.close()
            if engine is not None:
                engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=UI_DIR / "templates")
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
    app.include_router(target_router)
    app.include_router(transformation_router)
    app.include_router(artifact_router)
    app.include_router(model_router)
    app.include_router(dataset_router)
    app.include_router(conversation_router)
    app.include_router(document_router)
    app.include_router(version_router)
    app.include_router(provider_router)
    app.include_router(training_router)
    app.include_router(run_router)
    app.include_router(evaluation_router)
    app.include_router(deployment_router)
    app.state.soup_ingestion_url = settings.soup_ingestion_url

    @app.exception_handler(ExecutorError)
    async def executor_error(request: Request, error: ExecutorError) -> JSONResponse:
        explanation = explain(str(error))
        return JSONResponse(
            status_code=503,
            content={
                "detail": explanation.message + " " + explanation.action,
                "error": explanation.model_dump(),
                "advanced_detail": str(error),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, error: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"detail": str(error)},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        if request.url.path.startswith("/api/v1/execution-targets"):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "Invalid execution target settings; use an approved credential "
                    "reference and valid SSH or pinned Kubernetes image settings"
                },
                headers={"Cache-Control": "no-store"},
            )
        if request.url.path.startswith("/api/v1/llm-providers"):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Invalid provider settings; use a secret reference, "
                        "a plain HTTP(S) URL and explicit model names"
                    )
                },
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "type": item["type"],
                        "loc": list(item["loc"]),
                        "msg": "Invalid value; check this field",
                    }
                    for item in error.errors()
                ]
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(SourceError)
    async def source_error(request: Request, error: SourceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"detail": str(error)},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RegistryError)
    async def registry_error(request: Request, error: RegistryError) -> JSONResponse:
        explanation = explain(str(error))
        if explanation.code == "model_download":
            return JSONResponse(
                status_code=error.status,
                content={
                    "detail": explanation.message + " " + explanation.action,
                    "error": explanation.model_dump(),
                    "advanced_detail": str(error),
                },
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

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
        explanation = explain("", "storage_unavailable")
        detail = (
            "The file was not found. Check whether it was deleted or restore it from backup."
            if status == 404
            else "This file exceeds the size limit. Choose a smaller file or ask the operator "
            "to review the configured limit."
            if status == 413
            else explanation.message + " " + explanation.action
        )
        return JSONResponse(
            status_code=status,
            content={
                "detail": detail,
                "error": {
                    **explanation.model_dump(),
                    "code": "object_missing"
                    if status == 404
                    else "upload_too_large"
                    if status == 413
                    else explanation.code,
                    "message": detail,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Database unavailable; verify migrations and connectivity"},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        explanation = explain("")
        return JSONResponse(
            status_code=500,
            content={
                "detail": explanation.message + " " + explanation.action,
                "error": explanation.model_dump(),
            },
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

    @app.get("/models", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/models/{model_id}", response_class=HTMLResponse, include_in_schema=False)
    async def model_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="models.html", context={"app_name": settings.app_name}
        )

    @app.get("/datasets", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/datasets/{dataset_id}", response_class=HTMLResponse, include_in_schema=False)
    async def dataset_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="datasets.html", context={"app_name": settings.app_name}
        )

    @app.get(
        "/datasets/{dataset_id}/versions/{version_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def version_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="dataset-version.html", context={"app_name": settings.app_name}
        )

    @app.get("/settings/execution-targets", response_class=HTMLResponse, include_in_schema=False)
    async def targets_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="targets.html", context={"app_name": settings.app_name}
        )

    @app.get("/settings/providers", response_class=HTMLResponse, include_in_schema=False)
    async def provider_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="providers.html", context={"app_name": settings.app_name}
        )

    @app.get("/training-plans", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/training-plans/{plan_id}", response_class=HTMLResponse, include_in_schema=False)
    async def training_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="training-plans.html", context={"app_name": settings.app_name}
        )

    @app.get("/training-runs", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/training-runs/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    async def runs_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="training-runs.html", context={"app_name": settings.app_name}
        )

    @app.get("/evaluations", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/evaluations/{identifier}", response_class=HTMLResponse, include_in_schema=False)
    async def evaluation_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="evaluations.html", context={"app_name": settings.app_name}
        )

    @app.get("/deployments", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/deployments/{identifier}", response_class=HTMLResponse, include_in_schema=False)
    async def deployment_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="deployments.html", context={"app_name": settings.app_name}
        )

    return app
