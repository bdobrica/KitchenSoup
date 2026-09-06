"""Anonymous, bounded metadata resolution against the public Hugging Face host."""

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.registry.inspection import RegistryError, json_object, validate_structure


@dataclass(frozen=True)
class ResolvedModel:
    repository: str
    revision: str
    license: str
    license_url: str


class ModelResolver(Protocol):
    def resolve(self, repository: str, revision: str) -> ResolvedModel: ...


class HuggingFaceResolver:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client

    def _get(self, client: httpx.Client, path: str) -> dict[str, Any]:
        try:
            with client.stream("GET", "https://huggingface.co" + path) as response:
                if response.status_code in (401, 403, 404):
                    raise RegistryError(
                        422, "Use an existing public, ungated Hugging Face model and revision"
                    )
                if response.status_code != 200:
                    raise RegistryError(
                        503, "Hugging Face metadata is unavailable; try again later"
                    )
                data = bytearray()
                for chunk in response.iter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > 4 * 1024**2:
                        raise RegistryError(422, "Model metadata exceeds the inspection limit")
                return json_object(bytes(data))
        except httpx.HTTPError:
            raise RegistryError(
                503, "Hugging Face metadata is unavailable; try again later"
            ) from None

    def resolve(self, repository: str, revision: str) -> ResolvedModel:
        if self.client is not None:
            return self._resolve(self.client, repository, revision)
        # No implicit local token, proxy credentials, arbitrary host, or redirects.
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            return self._resolve(client, repository, revision)

    def _resolve(self, client: httpx.Client, repository: str, revision: str) -> ResolvedModel:
        repo = quote(repository, safe="/")
        metadata = self._get(client, f"/api/models/{repo}/revision/{quote(revision, safe='')}")
        if metadata.get("gated") is not False or metadata.get("private") is not False:
            raise RegistryError(422, "Only public, ungated models are supported")
        sha = metadata.get("sha")
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise RegistryError(422, "Hugging Face did not return an exact commit revision")
        siblings = metadata.get("siblings")
        if not isinstance(siblings, list):
            raise RegistryError(422, "Model file metadata is missing")
        names = {
            item["rfilename"]
            for item in siblings
            if isinstance(item, dict) and isinstance(item.get("rfilename"), str)
        }
        config = self._get(client, f"/{repo}/raw/{sha}/config.json")
        validate_structure(names, config)
        tokenizer = self._get(client, f"/{repo}/raw/{sha}/tokenizer_config.json")
        if tokenizer.get("auto_map"):
            raise RegistryError(422, "Custom tokenizer code is unsupported")
        card = metadata.get("cardData")
        license_name = card.get("license", "unknown") if isinstance(card, dict) else "unknown"
        if not isinstance(license_name, str) or not license_name.strip() or len(license_name) > 200:
            license_name = "unknown"
        return ResolvedModel(
            repository, sha, license_name, f"https://huggingface.co/{repo}/blob/{sha}/README.md"
        )
