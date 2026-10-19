"""Opt-in paid SageMaker FP16 smoke through the application playground proxy.

Requires preconfigured disposable ECR/S3/IAM, supplied without credentials in JSON.
Endpoint/model/config/staging cleanup is automatic; harness-owned infrastructure
must also be removed by its operator after this command.
"""

import argparse
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.session import create_database_engine
from app.deployments.sagemaker import SageMakerDeploymentExecutor
from app.deployments.schemas import DeploymentCreate
from app.executors.base import ExecutorError
from app.main import create_app
from app.registry.huggingface import HuggingFaceResolver
from app.registry.schemas import ArchiveImport
from app.services.artifacts import ArtifactService
from app.services.deployments import DeploymentService
from app.services.models import ModelService
from app.services.targets import TargetService
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore
from app.targets.sagemaker_schemas import SageMakerConfig
from app.targets.schemas import TargetCreate
from scripts.test_runpod_image import ROOT, generate_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--target-config", type=Path, required=True)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--minutes", type=int, default=15, choices=range(5, 16))
    args = parser.parse_args()
    if not args.confirm_paid:
        parser.error("--confirm-paid required; allocates a billable SageMaker endpoint")
    load_dotenv(ROOT / ".env")
    settings = Settings(
        postgres_host="127.0.0.1",
        storage_enabled=True,
        s3_endpoint="http://127.0.0.1:9000",
        s3_public_endpoint="http://127.0.0.1:9000",
        s3_access_key=SecretStr(os.environ["RUSTFS_ACCESS_KEY"]),
        s3_secret_key=SecretStr(os.environ["RUSTFS_SECRET_KEY"]),
        sagemaker_execution_enabled=True,
        aws_approved_profiles=args.profile,
    )
    engine = create_database_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    store = S3ArtifactStore(settings)
    artifacts = ArtifactService(store, factory, bucket=settings.s3_bucket)
    targets = TargetService(artifacts, settings)
    service = DeploymentService(artifacts, targets=targets)
    deployment = None
    try:
        store.provision(["http://127.0.0.1:8000", "http://localhost:8000"])
        config = SageMakerConfig.model_validate_json(args.target_config.read_bytes())
        if (
            config.instance_type != "ml.g4dn.xlarge"
            or args.minutes > config.max_deployment_lease_minutes
        ):
            raise RuntimeError("Smoke test requires one T4 and a bounded compatible lease")
        target = targets.create(
            TargetCreate(
                name="SageMaker serving smoke " + uuid4().hex,
                kind="sagemaker",
                credential_ref="aws-profile://" + args.profile,
                configuration=config,
            )
        )
        executor = targets.deployment(target.id)
        assert isinstance(executor, SageMakerDeploymentExecutor)
        with tempfile.TemporaryDirectory(prefix="kitchensoup-sagemaker-serving-") as temp:
            bundle = generate_fixture(Path(temp))
            payload = BytesIO()
            with ZipFile(payload, "w") as archive:
                for path in sorted((bundle / "model").iterdir()):
                    archive.write(path, path.name)
            data = payload.getvalue()
            grant = artifacts.initiate("model.zip", "application/zip", len(data))
            store.put(upload_key(grant.upload_id, "model.zip"), BytesIO(data))
            artifact = artifacts.complete(grant.upload_id)
            model = ModelService(factory, artifacts, HuggingFaceResolver()).import_archive(
                ArchiveImport(
                    name="Synthetic SageMaker serving base",
                    artifact_id=artifact.id,
                    license="CC0-1.0",
                )
            )
        deployment = service.create(
            DeploymentCreate(
                model_version_id=model.versions[0].id,
                execution_target_id=target.id,
                lease_minutes=args.minutes,
                precision="fp16",
            )
        )
        print("Durable deployment:", deployment.id, flush=True)
        deadline = time.monotonic() + args.minutes * 60
        service.process(deployment.id, "worker")
        while time.monotonic() < deadline:
            try:
                service.process(deployment.id, "reconciler")
            except ExecutorError:
                time.sleep(2)
                continue
            result = service.get(deployment.id)
            if result.status == "RUNNING":
                break
            if result.status in ("STOPPED", "FAILED", "LOST"):
                raise RuntimeError("Serving startup failed; inspect private serving evidence")
            time.sleep(2)
        else:
            raise RuntimeError("Serving startup exceeded deadline")
        body = {"messages": [{"role": "user", "content": "Hello"}]}
        with TestClient(create_app(settings, deployment_service=service)) as client:
            route = f"/api/v1/deployments/{deployment.id}/chat/completions"
            response = client.post(route, json=body)
            if response.status_code != 200 or not isinstance(response.json().get("content"), str):
                raise RuntimeError("Non-streaming playground inference failed")
            response = client.post(route, json={**body, "stream": True})
            if (
                response.status_code != 200
                or "[DONE]" not in response.text
                or "event: error" in response.text
            ):
                raise RuntimeError("Streaming playground inference failed")
            view = client.get(f"/api/v1/deployments/{deployment.id}").text
            if "arn:aws:" in view or "Bearer" in view:
                raise RuntimeError("Private serving metadata exposed")
        print("Authenticated non-streaming and streaming playground inference passed", flush=True)
    finally:
        try:
            if deployment is not None:
                service.action(deployment.id, "stop")
                for _ in range(180):
                    try:
                        service.process(deployment.id, "reconciler")
                    except ExecutorError:
                        time.sleep(5)
                        continue
                    result = service.get(deployment.id)
                    if result.status in (
                        "STOPPED",
                        "FAILED",
                        "LOST",
                    ):
                        break
                    time.sleep(5)
                else:
                    raise RuntimeError(
                        "AWS CLEANUP PENDING: keep reconciler running and inspect owned endpoint"
                    )
                print("Provider resource termination confirmed", flush=True)
        finally:
            store.close()
            engine.dispose()


if __name__ == "__main__":
    main()
