import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def wrapper() -> Any:
    path = Path(__file__).resolve().parents[2] / "images/vllm/sagemaker_serve.py"
    spec = importlib.util.spec_from_file_location("sagemaker_serve", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_container_verifies_pinned_manifest_and_exact_files(wrapper: Any, tmp_path: Path) -> None:
    settings = {"precision": "fp16", "adapter": False, "max_model_len": 2048}
    file = tmp_path / "serving.json"
    file.write_text(json.dumps(settings))
    raw = json.dumps({"serving.json": hashlib.sha256(file.read_bytes()).hexdigest()}).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert wrapper.verify(tmp_path, digest) == settings
    with pytest.raises(ValueError, match="identity"):
        wrapper.verify(tmp_path, "a" * 64)
    file.write_text("corrupt")
    with pytest.raises(ValueError, match="hash"):
        wrapper.verify(tmp_path, digest)
    file.write_text(json.dumps(settings))
    (tmp_path / "unreviewed.py").write_text("unsafe")
    with pytest.raises(ValueError, match="Unexpected"):
        wrapper.verify(tmp_path, digest)


def test_managed_routes_bound_and_fix_inference_parameters(
    wrapper: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def backend(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        wrapper.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(backend)),
    )
    process = Mock()
    process.poll.return_value = None
    with TestClient(wrapper.application(process, True)) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/v1/models").status_code == 404
        result = client.post(
            "/invocations",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "foreign",
                "max_tokens": 10000,
                "stream": True,
            },
        )
        assert result.status_code == 200
        payload = json.loads(requests[-1].content)
        assert payload["model"] == "tuned" and payload["max_tokens"] == 128
        assert payload["stream"] is False and payload["temperature"] == 0
        assert str(requests[-1].url) == "http://127.0.0.1:8000/v1/chat/completions"
        assert client.post("/invocations", content=b"x" * (128 * 1024 + 1)).status_code == 413
        assert client.post("/invocations", json={"messages": []}).status_code == 400
        assert (
            client.post(
                "/invocations", json={"messages": [{"role": "user", "content": "x" * 8001}]}
            ).status_code
            == 400
        )
        process.poll.return_value = 1
        assert client.get("/ping").status_code == 503
    process.terminate.assert_called_once()


@pytest.mark.parametrize("root", [False, True])
def test_private_cache_is_initialized_before_privilege_drop(
    wrapper: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: bool
) -> None:
    original = wrapper.tempfile.mkdtemp
    monkeypatch.setattr(
        wrapper.tempfile, "mkdtemp", lambda **kw: original(prefix=kw["prefix"], dir=tmp_path)
    )
    monkeypatch.setattr(wrapper.tempfile, "tempdir", None)
    monkeypatch.setattr(wrapper.os, "getuid", lambda: 0 if root else 10001)
    calls: list[str] = []
    for name in ("chown", "setgroups", "setgid", "setuid"):
        monkeypatch.setattr(wrapper.os, name, lambda *args, name=name: calls.append(name))
    names = (
        "HOME",
        "HF_HOME",
        "TORCHINDUCTOR_CACHE_DIR",
        "TRITON_CACHE_DIR",
        "VLLM_CACHE_ROOT",
        "XDG_CACHE_HOME",
        "CUDA_CACHE_PATH",
        "TMPDIR",
    )
    for name in names:
        monkeypatch.setenv(name, "old-cache")
    cache = wrapper.prepare_cache()
    assert cache.is_relative_to(tmp_path) and cache.stat().st_mode & 0o777 == 0o700
    for name in names:
        directory = Path(wrapper.os.environ[name])
        assert directory.is_relative_to(cache) and directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o700
    assert wrapper.tempfile.gettempdir() == str(cache / "tmp")
    assert calls == (["chown", "setgroups", "setgid", "setuid"] if root else [])
