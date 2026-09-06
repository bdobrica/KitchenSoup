import json

import httpx
import pytest
from pydantic import ValidationError

from app.ingestion.documents import DocumentSelection, SoupHTTPRunner, SoupResult, output_rows
from app.ingestion.sources import SourceError


def result(output: str = '{"text":"Synthetic example"}\n', **kwargs: object) -> SoupResult:
    data = {
        "schema": "kitchensoup.soup-ingest-response/v1",
        "soup_version": "0.74.0",
        "image_id": "sha256:" + "a" * 64,
        "exit_code": 0,
        "output": output,
        "stdout": "Wrote rows",
        "stderr": "",
        "logs_truncated": False,
    }
    data.update(kwargs)
    return SoupResult.model_validate(data)


def test_soup_output_validation_and_empty_row_accounting() -> None:
    rows, ignored = output_rows(result('{"text":"hello","page":0}\n{"text":" "}\n'))
    assert rows == [{"text": "hello", "page": 0}]
    assert ignored == 1
    for invalid in ("bad JSON", "[]", '{"text":null}', '{"other":"value"}'):
        with pytest.raises(SourceError):
            output_rows(result(invalid))


def test_selection_and_provenance_requirements() -> None:
    with pytest.raises(ValidationError):
        DocumentSelection(source_ids=[])
    with pytest.raises(ValidationError):
        result(image_id="latest")
    with pytest.raises(SourceError, match="disabled"):
        SoupHTTPRunner("").ingest(b"text", ".txt")


def test_http_runner_uses_fixed_route_and_no_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/ingest/.txt"
        assert request.content == b"synthetic input"
        return httpx.Response(200, json=result().model_dump(by_alias=True))

    def client(**kwargs: object) -> httpx.Client:
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", client)
    assert SoupHTTPRunner("http://runner").ingest(b"synthetic input", ".txt").exit_code == 0
    with pytest.raises(SourceError):
        SoupHTTPRunner("http://runner").ingest(b"x", "../../etc/passwd")


def test_generated_document_contracts() -> None:
    from pathlib import Path

    from app.ingestion.documents import DocumentIngestionManifest

    assert (
        json.loads(Path("docs/contracts/document-ingestion-v1.schema.json").read_text())
        == DocumentIngestionManifest.model_json_schema()
    )
