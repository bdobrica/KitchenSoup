"""Bounded Runpod REST v2 transport; never retries allocation requests."""

import json
import math
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.executors.base import ExecutorError
from app.providers.credentials import CredentialResolver
from app.providers.schemas import ProviderError
from app.targets.runpod_schemas import RunpodConfig


class AllocationRejected(ExecutorError):
    """A definitive non-allocating HTTP rejection, unlike transport uncertainty."""


class RunpodClient:
    def __init__(self, reference: str, resolver: CredentialResolver) -> None:
        self.reference, self.resolver = reference, resolver

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        try:
            key = self.resolver.resolve(self.reference)
            if key is None:
                raise ExecutorError("Runpod API credential is unavailable")
            # No redirects, environment proxies, unbounded reads or raw provider errors.
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False) as client:
                for attempt in range(3 if method == "GET" else 1):
                    with client.stream(
                        method,
                        "https://api.runpod.io/v2" + path,
                        json=body,
                        headers={"Authorization": "Bearer " + key.get_secret_value()},
                    ) as response:
                        if response.status_code == 404 and method in ("GET", "DELETE"):
                            return None
                        if method == "GET" and response.status_code in (429, 500, 502, 503, 504):
                            if attempt < 2:
                                time.sleep(0.25 * (attempt + 1))
                                continue
                        if response.status_code not in (200, 201, 204):
                            if method == "POST" and response.status_code in (
                                400,
                                401,
                                402,
                                403,
                                404,
                                413,
                                422,
                                429,
                            ):
                                raise AllocationRejected(
                                    "Runpod allocation rejected; check access, balance and capacity"
                                )
                            raise ExecutorError(
                                "Runpod request refused; check access, balance and capacity"
                            )
                        if response.status_code == 204:
                            return {}
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > 4 * 1024**2:
                                raise ExecutorError("Runpod response exceeds its limit")
                        value = json.loads(data)
                        if not isinstance(value, dict):
                            raise ValueError
                        return value
        except (httpx.HTTPError, ValueError, ProviderError):
            raise ExecutorError(
                "Runpod API unavailable; allocation/cleanup may remain pending"
            ) from None
        raise ExecutorError("Runpod API unavailable")

    @staticmethod
    def pod_id(value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
            raise ExecutorError("Runpod returned an invalid Pod identifier")
        return value

    def get(self, pod_id: str) -> dict[str, Any] | None:
        return self.request("GET", "/pods/" + self.pod_id(pod_id))

    def terminate(self, pod_id: str) -> None:
        self.request("DELETE", "/pods/" + self.pod_id(pod_id))

    def pods(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(20):
            page = self.request(
                "GET", "/pods?limit=100" + ("&cursor=" + quote(cursor, safe="") if cursor else "")
            )
            if page is None or not isinstance(page.get("pods"), list):
                raise ExecutorError("Runpod Pod inventory is unavailable")
            result.extend(page["pods"])
            pagination = page.get("pagination", {})
            if pagination.get("hasNextPage") is False:
                return result
            next_cursor = pagination.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        raise ExecutorError("Runpod Pod inventory exceeded its pagination bound")

    def quote(self, config: RunpodConfig) -> float:
        gpu = self.request(
            "GET",
            "/catalog/gpus/"
            + quote(config.gpu_id, safe="")
            + "?include=AVAILABILITY&product=POD&count=1&cloud="
            + config.cloud,
        )
        try:
            if gpu is None:
                raise ValueError
            price = float(gpu["price"][config.cloud.lower()])
            if (
                gpu["id"] != config.gpu_id
                or gpu["manufacturer"] != "NVIDIA"
                or gpu["memory"] < config.min_vram_gb
                or not gpu[config.cloud.lower()]
                or gpu.get("availability") not in ("LOW", "MEDIUM", "HIGH")
                or not math.isfinite(price)
                or not 0 < price <= config.max_hourly_price_usd
            ):
                raise ValueError
            if config.data_center_ids and not any(
                dc["id"] in config.data_center_ids
                and dc["availability"] in ("LOW", "MEDIUM", "HIGH")
                for dc in gpu.get("dataCenters", [])
            ):
                raise ValueError
            return price
        except (KeyError, TypeError, ValueError):
            raise ExecutorError(
                "Runpod GPU unavailable, under VRAM minimum or outside price/placement limits"
            ) from None
