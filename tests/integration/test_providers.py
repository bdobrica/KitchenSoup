import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import LLMProvider
from app.main import create_app
from app.providers.credentials import LocalCredentialResolver
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.schemas import ProviderConfig
from app.services.providers import ProviderService


@pytest.fixture
def client(
    factory: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "synthetic-integration-value")
    resolver = LocalCredentialResolver({"SYNTHETIC_PROVIDER_KEY"}, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer synthetic-integration-value"
        payload = json.loads(request.content)
        assert payload["model"] == "fixture-alias"
        assert len(payload["messages"]) == 1
        assert (
            "synthetic" in payload["messages"][0]["content"].lower()
            or "status" in payload["messages"][0]["content"]
        )
        content = '{"status":"ok"}' if "response_format" in payload else "OK"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    service = ProviderService(
        factory,
        lambda config: OpenAICompatibleProvider(config, resolver, httpx.MockTransport(handler)),
    )
    with TestClient(create_app(Settings(_env_file=None), provider_service=service)) as client:
        yield client


def test_provider_registration_update_and_tests_keep_only_references(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    body = {
        "name": "Synthetic provider",
        "base_url": "http://gateway:4000/prefix/v1/",
        "api_key_ref": "env://SYNTHETIC_PROVIDER_KEY",
        "model_names": ["fixture-alias"],
    }
    response = client.post("/api/v1/llm-providers", json=body)
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    provider = response.json()
    assert provider["base_url"] == "http://gateway:4000/prefix/v1"
    for mode in ("chat", "structured"):
        result = client.post(
            f"/api/v1/llm-providers/{provider['id']}/test",
            json={"model": "fixture-alias", "mode": mode},
        )
        assert result.status_code == 200 and result.json()["status"] == "ok"
        assert "synthetic-integration-value" not in result.text
    body["name"] = "Updated provider"
    updated = client.put(f"/api/v1/llm-providers/{provider['id']}", json=body)
    assert updated.status_code == 200 and updated.json()["id"] == provider["id"]
    assert updated.json()["created_at"] == provider["created_at"]
    listed = client.get("/api/v1/llm-providers")
    assert listed.json()[0]["name"] == "Updated provider"
    with factory() as session:
        row = session.scalar(select(LLMProvider))
        assert row and row.api_key_ref == "env://SYNTHETIC_PROVIDER_KEY"
        stored = {
            column.name: str(getattr(row, column.name)) for column in LLMProvider.__table__.columns
        }
        assert "synthetic-integration-value" not in json.dumps(stored)
    assert client.get("/settings/providers").status_code == 200


def test_invalid_inputs_never_echo_secret_values(client: TestClient) -> None:
    for body in (
        {"api_key": "synthetic-should-not-echo", "model_names": ["fixture-alias"]},
        {"api_key_ref": "synthetic-should-not-echo", "model_names": ["fixture-alias"]},
        {
            "base_url": "https://u:synthetic-should-not-echo@example.invalid",
            "model_names": ["fixture-alias"],
        },
    ):
        response = client.post("/api/v1/llm-providers", json=body)
        assert response.status_code == 422 and "synthetic-should-not-echo" not in response.text
    assert client.get("/api/v1/llm-providers").json() == []
    assert (
        client.post(
            f"/api/v1/llm-providers/{uuid4()}/test", json={"model": "fixture-alias"}
        ).status_code
        == 404
    )


def test_unconfigured_model_and_missing_secret_fail_closed(client: TestClient) -> None:
    config = ProviderConfig(
        model_names=["fixture-alias"], api_key_ref="env://SYNTHETIC_PROVIDER_KEY"
    )
    provider = client.post("/api/v1/llm-providers", json=config.model_dump()).json()
    assert (
        client.post(
            f"/api/v1/llm-providers/{provider['id']}/test", json={"model": "unknown"}
        ).status_code
        == 422
    )
    config.api_key_ref = "env://MISSING_KEY"
    client.put(f"/api/v1/llm-providers/{provider['id']}", json=config.model_dump())
    result = client.post(
        f"/api/v1/llm-providers/{provider['id']}/test", json={"model": "fixture-alias"}
    )
    assert result.status_code == 503
    assert (
        client.post(
            f"/api/v1/llm-providers/{provider['id']}/test",
            json={
                "model": "fixture-alias",
                "messages": [{"content": "never send arbitrary content"}],
            },
        ).status_code
        == 422
    )
