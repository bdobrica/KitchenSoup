"""Shared full-SSH transport and pre-provisioned host trust for owned Pods."""

import ipaddress
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.executors.base import ExecutorError
from app.targets.runpod_lifecycle import RunpodLifecycle
from app.targets.schemas import SshConfig
from app.targets.ssh import SshTransport


class RunpodConnection:
    lifecycle: RunpodLifecycle
    known_hosts: Path
    secret_directory: Path
    env_names: set[str]
    transport: SshTransport

    def connection_workspace(self, owner: UUID) -> Path:
        raise NotImplementedError

    def connect_pod(self, pod: dict[str, Any]) -> None:
        try:
            direct = pod["ssh"]["direct"]
            address = ipaddress.ip_address(direct["host"])
            if not address.is_global or not 1 <= int(direct["port"]) <= 65535:
                raise ValueError
            config = SshConfig(
                host=str(address),
                port=int(direct["port"]),
                username="runner",
                workspace="/home/runner/jobs",
            )
        except (KeyError, TypeError, ValueError):
            raise ExecutorError("Runpod full public-IP SSH networking is not ready") from None
        self.transport = SshTransport(
            config,
            self.lifecycle.config.ssh_credential_ref,
            known_hosts=self.known_hosts,
            secret_directory=self.secret_directory,
            env_names=self.env_names,
        )
        self.transport.transfer_timeout = min(1800, self.lifecycle.config.max_runtime_minutes * 60)

    def provisioned_trust(self, owner: UUID) -> None:
        row = self.lifecycle.resource(owner)
        assert row is not None
        self.transport.transfer_deadline = time.monotonic() + max(
            0, (row.expires_at - datetime.now(UTC)).total_seconds()
        )
        if self.lifecycle.config.ssh_host_trust != "provisioned-ephemeral":
            return
        public = row.request["env"]["KITCHENSOUP_HOST_PUBLIC_KEY"]
        config = self.transport.config
        host = config.host if config.port == 22 else f"[{config.host}]:{config.port}"
        path = self.connection_workspace(owner) / "known_hosts"
        with open(path, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            stream.write(host + " " + public + "\n")
        self.transport.known_hosts = path
