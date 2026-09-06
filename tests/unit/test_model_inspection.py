import json
import stat
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import httpx
import pytest
from pydantic import ValidationError

from app.registry.catalog import load_catalog
from app.registry.huggingface import HuggingFaceResolver
from app.registry.inspection import RegistryError, inspect_archive, validate_structure
from app.registry.schemas import Catalog, HuggingFaceImport


def test_packaged_catalog_and_schema() -> None:
    catalog = load_catalog()
    assert len(catalog.models) == 2
    assert all(len(model.revision) == 40 and not model.gated for model in catalog.models)
    duplicate = catalog.model_dump()
    duplicate["models"].append(duplicate["models"][0])
    with pytest.raises(ValidationError):
        Catalog.model_validate(duplicate)
    with pytest.raises(ValidationError):
        HuggingFaceImport(name="test", repository="https://other.example/model")


def test_valid_archive(model_archive: bytes) -> None:
    inspect_archive(BytesIO(model_archive))


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/absolute.txt",
        "model/evil\\name",
        "model/config.json",
        "model/code.py",
        "model/weights.bin",
    ],
)
def test_rejected_archive_members(model_archive: bytes, name: str) -> None:
    body = BytesIO(model_archive)
    with ZipFile(body, "a") as archive:
        archive.writestr(name, "ignored")
    with pytest.raises(RegistryError):
        inspect_archive(body)


def test_links_compression_limits_and_corruption(model_archive: bytes) -> None:
    body = BytesIO(model_archive)
    with ZipFile(body, "a") as archive:
        link = ZipInfo("model/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "config.json")
    with pytest.raises(RegistryError):
        inspect_archive(body)
    body = BytesIO(model_archive)
    with ZipFile(body, "a") as archive:
        archive.writestr("model/padding.txt", b"x" * 100000, compress_type=ZIP_DEFLATED)
    with pytest.raises(RegistryError):
        inspect_archive(body)
    with pytest.raises(RegistryError):
        inspect_archive(BytesIO(model_archive[:100]))


def test_missing_weights_shards_and_unknown_architecture(model_archive: bytes) -> None:
    names = {"tokenizer_config.json", "tokenizer.json", "model.safetensors"}
    with pytest.raises(RegistryError):
        validate_structure(names, {"model_type": "unknown", "architectures": [None]})
    with ZipFile(BytesIO(model_archive)) as original:
        body = BytesIO()
        with ZipFile(body, "w") as archive:
            for item in original.infolist():
                if not item.filename.endswith(".safetensors"):
                    archive.writestr(item.filename, original.read(item))
    with pytest.raises(RegistryError):
        inspect_archive(body)
    body = BytesIO(model_archive)
    with ZipFile(body, "a") as archive:
        archive.writestr(
            "model/model.safetensors.index.json", '{"weight_map":{"x":"missing.safetensors"}}'
        )
    with pytest.raises(RegistryError):
        inspect_archive(body)


@pytest.mark.parametrize("gated", [True, "auto", "manual"])
def test_huggingface_rejects_gated(gated: bool | str) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"gated": gated, "private": False})
        )
    )
    with client, pytest.raises(RegistryError, match="ungated"):
        HuggingFaceResolver(client).resolve("org/model", "main")


def test_huggingface_pins_requests_and_uses_no_credentials() -> None:
    requests = []
    sha = "a" * 40

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/api/models/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "gated": False,
                    "private": False,
                    "sha": sha,
                    "siblings": [
                        {"rfilename": name}
                        for name in [
                            "config.json",
                            "tokenizer_config.json",
                            "tokenizer.json",
                            "model.safetensors",
                        ]
                    ],
                    "cardData": {"license": "apache-2.0"},
                },
            )
        if request.url.path.endswith("tokenizer_config.json"):
            return httpx.Response(200, json={})
        return httpx.Response(
            200, json={"model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"]}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HuggingFaceResolver(client).resolve("org/model", "main")
    assert result.revision == sha and result.license == "apache-2.0"
    assert all(sha in request.url.path for request in requests[1:])
    assert all("authorization" not in request.headers for request in requests)


def test_catalog_schema_is_generated() -> None:
    from pathlib import Path

    assert (
        json.loads(Path("docs/contracts/model-catalog-v1.schema.json").read_text())
        == Catalog.model_json_schema()
    )


def test_rejects_ambiguous_json_and_invalid_tensor_ranges() -> None:
    from app.registry.inspection import json_object, validate_tensors

    with pytest.raises(RegistryError):
        json_object(b'{"model_type":"qwen2","model_type":"other"}')
    with pytest.raises(RegistryError):
        validate_tensors({"weight": {"shape": [2], "dtype": "F32", "data_offsets": [0, 4]}}, 4)
    with pytest.raises(RegistryError):
        validate_tensors({"weight": {"shape": [1], "dtype": "F32", "data_offsets": [4, 8]}}, 8)


def test_rejects_oversized_archive_directory() -> None:
    import struct

    end_record = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 4097, 4097, 0, 0, 0)
    with pytest.raises(RegistryError, match="directory"):
        inspect_archive(BytesIO(end_record))
