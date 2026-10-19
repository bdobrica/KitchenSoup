"""Worker command dispatch and independent external-job reconciliation."""

import argparse
import logging
import signal
from pathlib import Path
from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.session import create_database_engine
from app.dependencies import check_dependencies
from app.deployments.local import LocalDockerDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.evaluation.local import LocalInferenceRunner
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.executors.materialize import RegisteredInputMaterializer
from app.jobs.processor import JobProcessor
from app.jobs.queue import JobQueue
from app.services.artifacts import ArtifactService
from app.services.deployments import DeploymentService
from app.services.evaluations import EvaluationService
from app.services.runs import TrainingRunService
from app.services.training import TrainingPlanService
from app.services.transformations import TransformationService
from app.storage.s3 import S3ArtifactStore
from app.transformations.local import LocalTransformExecutor

HEARTBEAT = Path("/tmp/kitchensoup-heartbeat")


def main(settings: Settings | None = None, role: str | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("worker", "reconciler"))
    role = role if role is not None else parser.parse_args().role
    heartbeat_path = HEARTBEAT.with_name(f"kitchensoup-{role}-heartbeat")
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    settings = settings if settings is not None else Settings()
    if (
        settings.local_training_enabled
        or settings.local_deployment_enabled
        or settings.local_transformations_enabled
    ) and not settings.storage_enabled:
        raise ValueError("Local GPU execution requires artifact storage")
    check_dependencies(settings)
    engine = create_database_engine(settings)
    store = (
        S3ArtifactStore(settings)
        if (
            settings.local_training_enabled
            or settings.local_deployment_enabled
            or settings.local_transformations_enabled
        )
        else None
    )
    queue = JobQueue(settings.valkey_url)
    processor = None
    evaluations = None
    deployments = None
    transformations = None
    if store is not None:
        factory = sessionmaker(engine, expire_on_commit=False)
        artifacts = ArtifactService(
            store,
            factory,
            bucket=settings.s3_bucket,
            max_bytes=settings.upload_max_bytes,
            url_ttl=settings.storage_url_ttl,
        )
        if settings.local_transformations_enabled:
            transformations = TransformationService(
                artifacts,
                LocalTransformExecutor(
                    Path(settings.transformation_workspace),
                    DeploymentMaterializer(RegisteredInputMaterializer(artifacts)),
                    settings.transformation_image,
                ),
            )
        if settings.local_deployment_enabled:
            deployments = DeploymentService(
                artifacts,
                LocalDockerDeploymentExecutor(
                    Path(settings.deployment_workspace),
                    DeploymentMaterializer(RegisteredInputMaterializer(artifacts)),
                    image=settings.deployment_image,
                ),
            )
        if settings.local_training_enabled:
            executor = LocalDockerTrainingExecutor(
                Path(settings.training_workspace),
                RegisteredInputMaterializer(artifacts),
                image=settings.training_image,
                memory_gib=settings.training_memory_gib,
                cpus=settings.training_cpus,
                timeout=settings.training_timeout,
            )
            processor = JobProcessor(
                TrainingRunService(TrainingPlanService(factory), executor, queue, artifacts)
            )
            evaluations = EvaluationService(
                artifacts, LocalInferenceRunner(executor, RegisteredInputMaterializer(artifacts))
            )
    logging.info("%s ready; local training enabled=%s", role, processor is not None)

    def heartbeat() -> None:
        while not stopped.is_set():
            heartbeat_path.touch()
            stopped.wait(5)

    pulse = Thread(target=heartbeat, daemon=True)
    pulse.start()
    try:
        while not stopped.is_set():
            try:
                if processor is not None:
                    processor.tick(role)
                if role == "worker" and evaluations is not None:
                    evaluations.tick()
                if transformations is not None:
                    transformations.tick(role)
                if deployments is not None:
                    deployments.tick(role)
            except SQLAlchemyError:
                logging.warning("Job database unavailable; retrying")
            stopped.wait(2)
    finally:
        stopped.set()
        pulse.join(timeout=6)
        queue.close()
        if store is not None:
            store.close()
        engine.dispose()
        heartbeat_path.unlink(missing_ok=True)
        logging.info("%s stopped", role)


if __name__ == "__main__":
    main()
