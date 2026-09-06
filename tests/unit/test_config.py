from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KITCHENSOUP_APP_NAME", raising=False)
    assert Settings(_env_file=None).app_name == "KitchenSoup"


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KITCHENSOUP_APP_NAME", raising=False)
    (tmp_path / ".env").write_text("KITCHENSOUP_APP_NAME=From file\n", encoding="utf-8")
    assert Settings().app_name == "From file"
    monkeypatch.setenv("KITCHENSOUP_APP_NAME", "From environment")
    assert Settings().app_name == "From environment"


def test_empty_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KITCHENSOUP_APP_NAME", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
