"""Remote vLLM on a private Docker bridge, reached only through verified SSH."""

import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

from app.deployments.local import LocalDockerDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import DeploymentSpec
from app.targets.ssh import SshTransport


class SshDockerDeploymentExecutor(LocalDockerDeploymentExecutor):
    def __init__(
        self, root: Path, materializer: DeploymentMaterializer, transport: SshTransport
    ) -> None:
        super().__init__(root, materializer, image=transport.config.deployment_image)
        self.transport = transport

    def identity(self) -> tuple[int, int]:
        return self.transport.identity()

    def remote(self, identifier: UUID) -> str:
        return self.transport.config.workspace + "/deployments/" + str(identifier)

    def docker(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        args = list(arguments)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            identifier = UUID(name.removeprefix("kitchensoup-playground-"))
            local, remote = self.workspace(identifier), self.remote(identifier)
            self.transport.upload(local, remote)
            args = [arg.replace("src=" + str(local) + ",", "src=" + remote + ",") for arg in args]
        return self.transport.docker(args)

    def request(
        self, endpoint: str, method: str, path: str, payload: dict[str, Any] | None, timeout: int
    ) -> bytes:
        # The caller obtains endpoint solely from identity-verified Docker inspection.
        return self.transport.request(endpoint, method, path, payload, timeout)

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        super().stop(identifier, spec)
        self.transport.remove(self.remote(identifier))
