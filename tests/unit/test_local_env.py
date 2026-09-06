from pathlib import Path

from scripts.local_env import initialize


def test_initialize_preserves_configuration_and_existing_credentials(tmp_path: Path) -> None:
    example = tmp_path / "example"
    example.write_text("KITCHENSOUP_APP_NAME=Custom\nRUSTFS_ACCESS_KEY=existing-local-value\n")
    target = tmp_path / ".env"
    initialize(target, example)
    first = target.read_text()
    assert "KITCHENSOUP_APP_NAME=Custom\n" in first
    assert "RUSTFS_ACCESS_KEY=existing-local-value\n" in first
    assert "KITCHENSOUP_POSTGRES_PASSWORD=" in first
    assert "RUSTFS_SECRET_KEY=" in first
    initialize(target, example)
    assert target.read_text() == first


def test_initialize_fills_empty_values_without_duplicates(tmp_path: Path) -> None:
    example = tmp_path / "example"
    example.write_text(
        "RUSTFS_ACCESS_KEY=\nRUSTFS_SECRET_KEY=''\nKITCHENSOUP_POSTGRES_PASSWORD=\"\"\n"
    )
    target = tmp_path / ".env"
    initialize(target, example)
    entries = target.read_text().splitlines()
    assert len(entries) == 3
    assert all(len(entry.split("=", 1)[1]) == 48 for entry in entries)
