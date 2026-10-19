"""Shared Pod ownership, serialized reservations and conservative recovery."""

import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import paramiko
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Deployment, RunpodResource, TrainingRun
from app.executors.base import ExecutorError, JobStatus
from app.targets.runpod import AllocationRejected, RunpodClient
from app.targets.runpod_schemas import RunpodConfig


class RunpodLifecycle:
    def __init__(
        self,
        factory: sessionmaker[Session],
        target_id: UUID,
        config: RunpodConfig,
        client: RunpodClient,
        global_limit: int,
        host_key_directory: Path | None = None,
    ) -> None:
        self.factory, self.target_id, self.config, self.client = factory, target_id, config, client
        self.global_limit = global_limit
        self.host_key_directory = host_key_directory

    def resource(self, owner: UUID) -> RunpodResource | None:
        with self.factory() as session:
            row = session.scalar(select(RunpodResource).where(RunpodResource.owner_id == owner))
            if row is not None and row.target_id != self.target_id:
                raise ExecutorError("Runpod resource target mismatch")
            if row is not None:
                session.expunge(row)
            return row

    def training_cancel_requested(self, owner: UUID) -> bool:
        with self.factory() as session:
            return session.scalar(select(TrainingRun.status).where(TrainingRun.id == owner)) in (
                "CANCEL_REQUESTED",
                "CANCELED",
            )

    def deployment_stop_requested(self, owner: UUID) -> bool:
        with self.factory() as session:
            row = session.get(Deployment, owner)
            return (
                row is None or row.status == "STOP_REQUESTED" or row.expires_at <= datetime.now(UTC)
            )

    def reserve(self, owner: UUID, kind: str = "training") -> RunpodResource:
        c = self.config
        self.client.quote(c)
        with self.factory() as session, session.begin():
            # All Runpod targets share this transaction lock; an uncertain allocation
            # or unconfirmed termination continues to consume one capacity slot.
            session.execute(text("SELECT pg_advisory_xact_lock(74829301725001)"))
            if session.scalar(select(RunpodResource.id).where(RunpodResource.owner_id == owner)):
                raise ExecutorError("Runpod allocation was already attempted; reconcile or clone")
            active = (
                select(func.count())
                .select_from(RunpodResource)
                .where(RunpodResource.state != "RELEASED")
            )
            total = session.scalar(active) or 0
            target_count = (
                session.scalar(active.where(RunpodResource.target_id == self.target_id)) or 0
            )
            if total >= self.global_limit or target_count >= c.max_concurrent_pods:
                raise ExecutorError(
                    "Runpod capacity limit reached; pending cleanup also consumes capacity"
                )
            resource_id, nonce = uuid4(), uuid4().hex
            request: dict[str, Any] = {
                "name": "kitchensoup-" + str(resource_id),
                "image": c.training_image,
                "gpu": {"id": c.gpu_id, "count": 1},
                "cloud": c.cloud,
                "dataCenterIds": c.data_center_ids,
                "disk": c.disk_gb,
                "ports": ["22/tcp", "8000/http"] if kind == "deployment" else ["22/tcp"],
                "startSsh": True,
                "env": {"KITCHENSOUP_RESOURCE": str(resource_id), "KITCHENSOUP_NONCE": nonce},
            }
            if c.ssh_host_trust == "provisioned-ephemeral":
                if self.host_key_directory is None:
                    raise ExecutorError("Private Runpod host-key staging is required")
                self.host_key_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                key_path = self.host_key_directory / (str(resource_id) + ".key")
                key = paramiko.RSAKey.generate(3072)
                with open(
                    key_path,
                    "x",
                    opener=lambda path, flags: os.open(path, flags, 0o600),
                ) as stream:
                    key.write_private_key(stream)
                request["env"]["KITCHENSOUP_HOST_PUBLIC_KEY"] = (
                    key.get_name() + " " + key.get_base64()
                )
            deadline = datetime.now(UTC) + timedelta(minutes=c.max_runtime_minutes)
            if kind == "deployment":
                deployment = session.get(Deployment, owner)
                if deployment is None or deployment.expires_at <= datetime.now(UTC):
                    raise ExecutorError("Deployment lease expired before allocation")
                deadline = deployment.created_at + timedelta(minutes=c.max_runtime_minutes)
            row = RunpodResource(
                id=resource_id,
                target_id=self.target_id,
                owner_id=owner,
                kind=kind,
                state="INTENT",
                cleanup="NONE",
                request=request,
                pod_ids=[],
                expires_at=deadline,
            )
            session.add(row)
        result = self.resource(owner)
        assert result is not None
        return result

    @staticmethod
    def owned(row: RunpodResource, pod: dict[str, Any]) -> bool:
        return (
            pod.get("name") == row.request["name"]
            and pod.get("image") == row.request["image"]
            and all(pod.get("env", {}).get(k) == v for k, v in row.request["env"].items())
        )

    @staticmethod
    def compatible(row: RunpodResource, pod: dict[str, Any]) -> bool:
        return (
            pod.get("gpu", {}).get("id") == row.request["gpu"]["id"]
            and pod.get("gpu", {}).get("count") == 1
            and pod.get("cloud") == row.request["cloud"]
            and pod.get("disk") == row.request["disk"]
            and (
                not row.request["dataCenterIds"]
                or pod.get("dataCenterId") in row.request["dataCenterIds"]
            )
        )

    def bind(self, owner: UUID, pods: list[dict[str, Any]]) -> None:
        with self.factory() as session, session.begin():
            row = session.scalar(
                select(RunpodResource).where(RunpodResource.owner_id == owner).with_for_update()
            )
            assert row is not None
            if not pods or any(not self.owned(row, pod) for pod in pods):
                raise ExecutorError("Runpod Pod identity does not match retained ownership")
            row.pod_ids = sorted(set(row.pod_ids) | {self.client.pod_id(pod["id"]) for pod in pods})
            row.state = "BOUND"

    def create(self, owner: UUID, kind: str = "training") -> None:
        row = self.reserve(owner, kind)
        request = {**row.request, "env": dict(row.request["env"])}
        if self.config.ssh_host_trust == "provisioned-ephemeral":
            assert self.host_key_directory is not None
            # Per-allocation bootstrap key is short-lived. Only its public half is
            # persisted in PostgreSQL; neither half comes from network enrollment.
            request["env"]["KITCHENSOUP_SSH_HOST_KEY"] = (
                self.host_key_directory / (str(row.id) + ".key")
            ).read_text()
        # Intent commits before POST. Even a 5xx is never retried on this attempt.
        try:
            pod = self.client.request("POST", "/pods", request)
        except AllocationRejected:
            self.pending(owner, JobStatus(state="FAILED", message="Runpod rejected allocation"))
            self.release(row.id)
            raise
        if pod is None:
            raise ExecutorError("Runpod allocation response is uncertain")
        self.bind(owner, [pod])
        if not self.compatible(row, pod):
            self.pending(
                owner,
                JobStatus(
                    state="FAILED", message="Allocated Pod configuration differs from target"
                ),
            )
            self.cleanup(owner)
            raise ExecutorError("Incompatible owned Runpod allocation; termination requested")
        try:
            price = float(pod["cost"])
            if not math.isfinite(price) or not 0 < price <= self.config.max_hourly_price_usd:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            self.pending(
                owner,
                JobStatus(
                    state="FAILED", message="Allocated Pod price is unavailable or exceeds ceiling"
                ),
            )
            self.cleanup(owner)
            raise ExecutorError(
                "Runpod allocation price failed validation; termination requested"
            ) from None

    def inspect(self, owner: UUID) -> dict[str, Any] | None:
        row = self.resource(owner)
        if row is None or row.state == "RELEASED":
            return None
        if not row.pod_ids:
            matches = [pod for pod in self.client.pods() if self.owned(row, pod)]
            if not matches:
                # Even a complete empty list cannot prove a timed-out POST will
                # never allocate. Keep the reservation and warn rather than retry.
                self.warning(
                    owner, "Allocation uncertain; no verified Pod found; capacity remains reserved"
                )
                raise ExecutorError("Runpod allocation uncertain; cleanup remains pending")
            self.bind(owner, matches)
            row = self.resource(owner)
            assert row is not None
        if len(row.pod_ids) != 1:
            self.pending(owner, JobStatus(state="FAILED"))
            raise ExecutorError("Multiple owned Pods found; cleanup pending")
        pod = self.client.get(row.pod_ids[0])
        if pod is not None and not self.owned(row, pod):
            raise ExecutorError("Runpod Pod ownership changed; refusing remote access")
        if pod is not None and not self.compatible(row, pod):
            self.pending(
                owner,
                JobStatus(state="FAILED", message="Owned Pod no longer meets target configuration"),
            )
            self.cleanup(owner)
            raise ExecutorError("Incompatible owned Runpod Pod; termination requested")
        return pod

    def warning(self, owner: UUID, message: str) -> None:
        with self.factory() as session, session.begin():
            row = session.scalar(select(RunpodResource).where(RunpodResource.owner_id == owner))
            assert row is not None
            row.warning = message

    def pending(self, owner: UUID, outcome: JobStatus) -> None:
        with self.factory() as session, session.begin():
            row = session.scalar(
                select(RunpodResource).where(RunpodResource.owner_id == owner).with_for_update()
            )
            assert row is not None
            if row.state == "RELEASED":
                return
            if row.outcome is None:
                row.outcome = outcome.model_dump(mode="json")
            row.cleanup = "PENDING"
            row.warning = "Pod termination pending; provider charges may continue"

    def cleanup(self, owner: UUID) -> None:
        row = self.resource(owner)
        if row is None or row.state == "RELEASED":
            return
        if not row.pod_ids:
            self.inspect(owner)
            row = self.resource(owner)
            assert row is not None
        for identifier in row.pod_ids:
            pod = self.client.get(identifier)
            if pod is not None:
                if not self.owned(row, pod):
                    raise ExecutorError("Runpod ownership changed; refusing termination")
                self.client.terminate(identifier)
                if self.client.get(identifier) is not None:
                    raise ExecutorError("Runpod termination not yet confirmed")
        self.release(row.id)

    def release(self, resource_id: UUID) -> None:
        with self.factory() as session, session.begin():
            saved = session.get(RunpodResource, resource_id)
            assert saved is not None
            saved.state, saved.cleanup, saved.warning = "RELEASED", "CONFIRMED", None
        if self.host_key_directory is not None:
            (self.host_key_directory / (str(resource_id) + ".key")).unlink(missing_ok=True)
