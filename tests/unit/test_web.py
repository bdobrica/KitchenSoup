from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_contract() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"status": "ok"}


def test_home_and_assets_work_outside_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KITCHENSOUP_APP_NAME", raising=False)
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "KitchenSoup" in response.text
        assert "htmx.org@" in response.text
        assert "alpinejs@" in response.text
        assert client.get("/static/styles.css").status_code == 200


def test_configured_name_is_html_escaped() -> None:
    settings = Settings(_env_file=None, app_name="<script>alert(1)</script>")
    with TestClient(create_app(settings)) as client:
        page = client.get("/").text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
