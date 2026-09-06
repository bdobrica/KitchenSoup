from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app import dependencies
from app.config import Settings
from app.main import create_app


def settings() -> Settings:
    return Settings(
        _env_file=None, check_dependencies=True, postgres_password=SecretStr("test-only")
    )


def test_checks_are_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(dependencies, "check_postgres", probe)
    dependencies.check_dependencies(Settings(_env_file=None, check_dependencies=False))
    probe.assert_not_called()


def test_startup_checks_all_services(monkeypatch: pytest.MonkeyPatch) -> None:
    probes = [Mock(), Mock(), Mock()]
    for name, probe in zip(("check_postgres", "check_valkey", "check_rustfs"), probes, strict=True):
        monkeypatch.setattr(dependencies, name, probe)
    config = settings()
    with TestClient(create_app(config)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
    for probe in probes:
        probe.assert_called_once_with(config)


@pytest.mark.parametrize("failed", ["check_postgres", "check_valkey", "check_rustfs"])
def test_dependency_failure_prevents_startup_without_logging_details(
    monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    for name in ("check_postgres", "check_valkey", "check_rustfs"):
        monkeypatch.setattr(dependencies, name, Mock())
    monkeypatch.setattr(
        dependencies, failed, Mock(side_effect=OSError("sensitive connection value"))
    )
    with pytest.raises(RuntimeError, match="startup check failed") as error:
        with TestClient(create_app(settings())):
            pytest.fail("Application should not start")
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__


def test_password_required_for_dependency_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KITCHENSOUP_POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="password is required"):
        Settings(_env_file=None, check_dependencies=True)


def test_password_is_redacted() -> None:
    assert "test-only" not in repr(settings())
