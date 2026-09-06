from pathlib import Path

import httpx
import pytest

from app.executors import materialize
from app.executors.base import ExecutorError
from app.executors.materialize import download_model


def test_hub_materialization_uses_exact_commit_and_no_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "authorization" not in request.headers
        if request.url.path.startswith("/api/"):
            return httpx.Response(
                200,
                json={
                    "sha": "a" * 40,
                    "gated": False,
                    "private": False,
                    "siblings": [
                        {"rfilename": "config.json"},
                        {"rfilename": "model.safetensors"},
                        {"rfilename": "unsafe.py"},
                    ],
                },
            )
        assert "/resolve/" + "a" * 40 + "/" in request.url.path
        return httpx.Response(200, content=b"fixture")

    client = httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    download_model("fixture/model", "a" * 40, tmp_path)
    assert len(calls) == 3
    assert not (tmp_path / "unsafe.py").exists()
    assert (tmp_path / "model.safetensors").read_bytes() == b"fixture"


@pytest.mark.parametrize(
    "location",
    [
        "http://huggingface.co/model",
        "https://127.0.0.1/model",
        "https://huggingface.co.evil.example/model",
    ],
)
def test_hub_download_rejects_untrusted_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": location})
        )
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    with pytest.raises(ExecutorError, match="allowed hosts"):
        download_model("fixture/model", "a" * 40, tmp_path)


def test_hub_download_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materialize, "MAX_MODEL", 2)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"oversized"))
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    with pytest.raises(ExecutorError, match="resource limits"):
        download_model("fixture/model", "a" * 40, tmp_path)
