"""Read short-lived credential values only from operator-approved locations."""

import os
import stat
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from app.providers.schemas import ProviderConfig, ProviderError

MAX_SECRET = 8192


class CredentialResolver(Protocol):
    def resolve(self, reference: str | None) -> SecretStr | None: ...


class LocalCredentialResolver:
    def __init__(self, env_names: set[str], secret_directory: Path) -> None:
        self.env_names = env_names
        self.secret_directory = secret_directory

    def resolve(self, reference: str | None) -> SecretStr | None:
        if reference is None:
            return None
        try:
            ProviderConfig.validate_reference(reference)
            if reference.startswith("env://"):
                name = reference[6:]
                if name not in self.env_names:
                    raise ProviderError(
                        503, "Credential environment name is not enabled by the operator"
                    )
                value = os.environ.get(name, "")
            else:
                root = self.secret_directory.resolve(strict=True)
                path = Path(reference[7:]).resolve(strict=True)
                if not path.is_relative_to(root) or path == root:
                    raise ProviderError(
                        503, "Credential file must be under the operator secret directory"
                    )
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ProviderError(503, "Credential file must be a regular file")
                    data = stream.read(MAX_SECRET + 1)
                    if len(data) > MAX_SECRET:
                        raise ProviderError(503, "Credential file exceeds its size limit")
                    value = data.decode("utf-8").rstrip("\r\n")
            if (
                not value
                or len(value.encode()) > MAX_SECRET
                or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
            ):
                raise ProviderError(503, "Credential is missing or not a valid bearer token")
            return SecretStr(value)
        except (OSError, ValueError):
            raise ProviderError(503, "Credential reference could not be resolved") from None
