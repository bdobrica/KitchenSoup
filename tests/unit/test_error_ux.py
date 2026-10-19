from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import explain
from app.executors.base import ExecutorError, LogChunk
from app.main import create_app
from app.storage.base import ObjectNotFound, ObjectTooLarge, StorageError


@pytest.mark.parametrize(
    ("detail", "code"),
    [
        ("torch.OutOfMemoryError: CUDA out of memory", "gpu_memory"),
        ("Pod OOMKilled", "target_memory"),
        ("Public model download failed; verify availability", "model_download"),
        ("403 GatedRepoError", "model_download"),
        ("Hugging Face download failed", "model_download"),
        ("Object storage operation failed", "storage_unavailable"),
        ("SSH connection timed out", "executor_unavailable"),
        ("unexpected engine failure", "operation_failed"),
    ],
)
def test_explanations_are_actionable_without_echoing_payload(detail: str, code: str) -> None:
    result = explain(detail + " synthetic-private-payload")
    assert result.code == code and result.action
    assert "synthetic-private-payload" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (StorageError("synthetic-private-payload"), 503, "storage_unavailable"),
        (ObjectNotFound("synthetic-private-payload"), 404, "object_missing"),
        (ObjectTooLarge("synthetic-private-payload"), 413, "upload_too_large"),
        (RuntimeError("synthetic-private-payload"), 500, "operation_failed"),
    ],
)
def test_http_errors_never_echo_unknown_payloads(error: Exception, status: int, code: str) -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/test-error")
    def fail() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-error")
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert "synthetic-private-payload" not in response.text
        assert response.headers["cache-control"] == "no-store"


def test_normalized_executor_details_are_separate_and_validation_inputs_omitted() -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/test-executor")
    def fail() -> None:
        raise ExecutorError("SSH connection timed out")

    with TestClient(app) as client:
        value = client.get("/test-executor").json()
        assert value["error"]["code"] == "executor_unavailable"
        assert value["advanced_detail"] == "SSH connection timed out"
        # Supply a route without storage dependency to exercise body validation.
        from app.api.artifacts import UploadRequest

        @app.post("/test-validation")
        def validate(body: UploadRequest) -> None:
            pass

        response = client.post(
            "/test-validation", json={"filename": "x", "size_bytes": "synthetic-private-payload"}
        )
        assert response.status_code == 422
        assert "synthetic-private-payload" not in response.text
        assert all("input" not in item and "ctx" not in item for item in response.json()["detail"])
        assert "Advanced details and raw logs" in client.get("/training-runs").text
        assert "Endpoint exposure" in client.get("/deployments").text
        assert "Data sharing" in client.get("/settings/providers").text


def test_diagnostics_reads_bounded_logs_without_lifecycle_actions() -> None:
    from app.api.runs import run_diagnostics

    class FakeRuns:
        status = "FAILED"
        calls = 0

        def get(self, identifier: object) -> Any:
            return self

        def logs(self, identifier: object, stream: str, cursor: int) -> LogChunk:
            self.calls += 1
            assert stream == "stderr"
            if cursor == 0:
                return LogChunk(text="Synthetic normal startup\n", cursor=65536)
            return LogChunk(text="CUDA out of memory: synthetic input", cursor=65550)

    fake = FakeRuns()
    result = run_diagnostics(uuid4(), fake)  # type: ignore[arg-type]
    assert result is not None and result.code == "gpu_memory" and fake.calls == 2
    fake.status = "RUNNING"
    assert run_diagnostics(uuid4(), fake) is None  # type: ignore[arg-type]
    assert fake.calls == 2
