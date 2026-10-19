"""Explicitly opted-in, bounded paid training through the durable application lifecycle."""

import argparse
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from dotenv import load_dotenv
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.session import create_database_engine
from app.executors.base import JobStatus
from app.executors.runpod import RunpodTrainingExecutor
from app.ingestion.schemas import DatasetCreate, SourceAttach
from app.ingestion.versions import DatasetVersionCreate
from app.jobs.locking import claim
from app.jobs.processor import JobProcessor
from app.registry.huggingface import HuggingFaceResolver
from app.registry.schemas import ArchiveImport
from app.services.artifacts import ArtifactService
from app.services.conversations import ConversationService
from app.services.datasets import DatasetService
from app.services.models import ModelService
from app.services.runs import RunCreate, TrainingRunService
from app.services.targets import TargetService
from app.services.training import TrainingPlanService
from app.services.versions import DatasetVersionService
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore
from app.targets.runpod_schemas import RunpodConfig
from app.targets.schemas import TargetCreate
from app.training.schemas import AppSpec, TrainingOverrides
from scripts.test_runpod_image import ROOT, generate_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument(
        "--image", required=True, help="Public SSH trainer repository@sha256:digest"
    )
    parser.add_argument("--gpu-id", default="Tesla V100-PCIE-16GB")
    parser.add_argument("--precision", choices=("fp16", "bf16"), default="fp16")
    parser.add_argument("--cloud", choices=("COMMUNITY", "SECURE"), default="COMMUNITY")
    parser.add_argument("--min-vram-gb", type=int, default=8)
    parser.add_argument("--max-hourly-usd", type=float, default=0.3)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--budget-usd", type=float, default=3)
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh/id_ed25519")
    args = parser.parse_args()
    if not args.confirm_paid:
        parser.error("--confirm-paid is required; this test allocates a billable GPU Pod")
    if (
        not 0 < args.budget_usd <= 3
        or args.max_hourly_usd * args.minutes / 60 > args.budget_usd / 2
    ):
        parser.error(
            "GPU estimate must leave half the budget for storage/other charges; budget <= $3"
        )
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
    config = RunpodConfig(
        ssh_credential_ref="file://" + str(key_path),
        ssh_host_trust="provisioned-ephemeral",
        gpu_id=args.gpu_id,
        allowed_gpu_ids=[args.gpu_id],
        min_vram_gb=args.min_vram_gb,
        cloud=args.cloud,
        training_image=args.image,
        max_hourly_price_usd=args.max_hourly_usd,
        max_runtime_minutes=args.minutes,
    )
    engine = create_database_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    store = S3ArtifactStore(settings)
    artifacts = ArtifactService(store, factory, bucket=settings.s3_bucket)
    targets = TargetService(artifacts, settings)
    service = TrainingRunService(
        TrainingPlanService(factory), None, artifacts=artifacts, targets=targets
    )
    run = None
    try:
        store.provision(["http://127.0.0.1:8000", "http://localhost:8000"])
        target = targets.create(
            TargetCreate(
                name="Runpod FP16 smoke " + uuid4().hex,
                kind="runpod_pod",
                credential_ref="env://RUNPOD_API_KEY",
                configuration=config,
            )
        )
        quote = targets.test(target.id)
        print(quote.model_dump_json(), flush=True)

        def upload(filename: str, data: bytes):
            grant = artifacts.initiate(filename, "application/zip", len(data))
            store.put(upload_key(grant.upload_id, filename), BytesIO(data))
            return artifacts.complete(grant.upload_id)

        with tempfile.TemporaryDirectory(prefix="kitchensoup-runpod-fixture-") as temp:
            bundle = generate_fixture(Path(temp))
            model_bytes = BytesIO()
            with ZipFile(model_bytes, "w") as archive:
                for path in sorted((bundle / "model").iterdir()):
                    archive.write(path, path.name)
            model = ModelService(factory, artifacts, HuggingFaceResolver()).import_archive(
                ArchiveImport(
                    name="Synthetic Runpod base",
                    artifact_id=upload("model.zip", model_bytes.getvalue()).id,
                    license="CC0-1.0",
                )
            )
        datasets = DatasetService(factory, artifacts)
        dataset = datasets.create(DatasetCreate(name="Synthetic Runpod smoke"))
        export = BytesIO()
        with ZipFile(export, "w") as archive:
            archive.write(ROOT / "tests/fixtures/chatgpt/conversations.json", "conversations.json")
        source = datasets.attach(
            dataset.id,
            SourceAttach(
                artifact_id=upload("export.zip", export.getvalue()).id, filename="export.zip"
            ),
        )
        conversations = ConversationService(datasets)
        conversations.import_source(dataset.id, source.id)
        version = DatasetVersionService(datasets).create(
            dataset.id,
            DatasetVersionCreate(
                conversation_ids=[item.id for item in conversations.list_conversations(dataset.id)]
            ),
        )
        plan = service.plans.create(
            AppSpec(
                intent="conversation_imitation",
                base_model_version_id=model.versions[0].id,
                dataset_version_id=version.id,
                overrides=TrainingOverrides(
                    precision=args.precision,
                    max_sequence_length=256,
                    epochs=1,
                    batch_size=1,
                    gradient_accumulation_steps=1,
                    lora_rank=2,
                    lora_alpha=4,
                ),
            )
        )
        run = service.create(RunCreate(plan_id=plan.id, execution_target_id=target.id))
        print("Durable synthetic run:", run.id, flush=True)
        processor = JobProcessor(service)
        deadline = time.monotonic() + args.minutes * 60
        while time.monotonic() < deadline:
            processor.tick("worker")
            processor.tick("reconciler")
            result = service.get(run.id)
            if result.status in ("SUCCEEDED", "FAILED", "CANCELED", "LOST"):
                if result.status != "SUCCEEDED" or result.model_version_id is None:
                    raise RuntimeError(
                        "Runpod synthetic training failed; inspect retained run logs"
                    )
                print("Verified adapter registered:", result.model_version_id, flush=True)
                break
            time.sleep(2)
        else:
            raise RuntimeError("Runpod smoke exceeded its deadline")
    finally:
        try:
            if run is not None:
                if service.get(run.id).status in ("QUEUED", "PREPARING", "SUBMITTED", "RUNNING"):
                    # Prevent an interrupted smoke's queued intent from being
                    # allocated later by a separately restarted command worker.
                    service.cancel(run.id)
                executor = service.executor_for(run.id)
                assert isinstance(executor, RunpodTrainingExecutor)
                for _ in range(10):
                    with claim(factory, run.id) as acquired:
                        if acquired:
                            resource = executor.lifecycle.resource(run.id)
                            if resource is None or resource.cleanup == "CONFIRMED":
                                break
                            executor.lifecycle.pending(run.id, JobStatus(state="CANCELED"))
                            try:
                                executor.lifecycle.cleanup(run.id)
                            except Exception:
                                pass
                    time.sleep(2)
                resource = executor.lifecycle.resource(run.id)
                if resource is not None and resource.cleanup != "CONFIRMED":
                    raise RuntimeError(
                        "RUNPOD CLEANUP PENDING: restart reconciler and inspect "
                        "owned resources immediately"
                    )
                print("Provider resource termination confirmed", flush=True)
        finally:
            store.close()
            engine.dispose()


if __name__ == "__main__":
    main()
