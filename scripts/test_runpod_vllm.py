"""Explicit paid synthetic inference smoke through the real application playground proxy."""

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
from app.deployments.runpod import RunpodDeploymentExecutor
from app.deployments.schemas import DeploymentCreate
from app.main import create_app
from app.registry.huggingface import HuggingFaceResolver
from app.registry.schemas import ArchiveImport
from app.services.artifacts import ArtifactService
from app.services.deployments import DeploymentService
from app.services.models import ModelService
from app.services.targets import TargetService
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore
from app.targets.runpod_schemas import RunpodConfig
from app.targets.schemas import TargetCreate
from scripts.test_runpod_image import ROOT, generate_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--image", required=True, help="Public SSH vLLM repository@sha256:digest")
    parser.add_argument("--training-image", required=True, help="Separate pinned trainer image")
    parser.add_argument("--gpu-id", required=True)
    parser.add_argument("--cloud", choices=("COMMUNITY", "SECURE"), default="SECURE")
    parser.add_argument("--precision", choices=("fp16", "bf16"), default="bf16")
    parser.add_argument("--min-vram-gb", type=int, default=8)
    parser.add_argument("--max-hourly-usd", type=float, default=0.5)
    parser.add_argument("--disk-gb", type=int, default=50)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--budget-usd", type=float, default=3)
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh/id_ed25519")
    args = parser.parse_args()
    if not args.confirm_paid:
        parser.error("--confirm-paid required; allocates a billable GPU Pod")
    if (
        not 0 < args.budget_usd <= 3
        or args.max_hourly_usd * args.minutes / 60 > args.budget_usd / 2
    ):
        parser.error("GPU estimate must leave half the <= $3 budget for other charges")
    load_dotenv(ROOT / ".env")
    key_path = args.ssh_key.resolve(strict=True)
    settings = Settings(
        postgres_host="127.0.0.1",
        storage_enabled=True,
        s3_endpoint="http://127.0.0.1:9000",
        s3_public_endpoint="http://127.0.0.1:9000",
        s3_access_key=SecretStr(os.environ["RUSTFS_ACCESS_KEY"]),
        s3_secret_key=SecretStr(os.environ["RUSTFS_SECRET_KEY"]),
        runpod_execution_enabled=True,
        runpod_env_names="RUNPOD_API_KEY",
        ssh_secret_directory=str(key_path.parent),
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
        config = RunpodConfig(
            ssh_credential_ref="file://" + str(key_path),
            ssh_host_trust="provisioned-ephemeral",
            gpu_id=args.gpu_id,
            allowed_gpu_ids=[args.gpu_id],
            min_vram_gb=args.min_vram_gb,
            cloud=args.cloud,
            training_image=args.training_image,
            deployment_image=args.image,
            deployment_disk_gb=args.disk_gb,
            max_hourly_price_usd=args.max_hourly_usd,
            max_deployment_lease_minutes=args.minutes,
        )
        target = targets.create(
            TargetCreate(
                name="Runpod serving smoke " + uuid4().hex,
                kind="runpod_pod",
                credential_ref="env://RUNPOD_API_KEY",
                configuration=config,
            )
        )
        executor = targets.deployment(target.id)
        assert isinstance(executor, RunpodDeploymentExecutor)
        price = executor.lifecycle.client.quote(executor.lifecycle.config)
        print(f"Quoted GPU compute: ${price:.2f}/hour; maximum {args.minutes} minutes", flush=True)
        with tempfile.TemporaryDirectory(prefix="kitchensoup-runpod-serving-") as temp:
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
                    name="Synthetic Runpod serving base", artifact_id=artifact.id, license="CC0-1.0"
                )
            )
        deployment = service.create(
            DeploymentCreate(
                model_version_id=model.versions[0].id,
                execution_target_id=target.id,
                lease_minutes=args.minutes,
                precision=args.precision,
            )
        )
        print("Durable deployment:", deployment.id, flush=True)
        deadline = time.monotonic() + args.minutes * 60
        service.tick("worker")
        owned = executor.lifecycle.resource(deployment.id)
        if owned is not None:
            print("Owned Pods:", ", ".join(owned.pod_ids), flush=True)
        while time.monotonic() < deadline:
            service.tick("reconciler")
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
            if "proxy.runpod.net" in view or "Bearer" in view:
                raise RuntimeError("Private serving metadata exposed")
        print("Authenticated non-streaming and streaming playground inference passed", flush=True)
    finally:
        try:
            if deployment is not None:
                service.action(deployment.id, "stop")
                for _ in range(10):
                    service.tick("reconciler")
                    result = service.get(deployment.id)
                    if result.resource_cleanup in (None, "CONFIRMED") and result.status in (
                        "STOPPED",
                        "FAILED",
                        "LOST",
                    ):
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError(
                        "RUNPOD CLEANUP PENDING: restart reconciler and inspect owned resources"
                    )
                print("Provider resource termination confirmed", flush=True)
        finally:
            store.close()
            engine.dispose()


if __name__ == "__main__":
    main()
