from pathlib import Path

import pytest
import yaml

from scripts.release_images import release_version, render_compose

ROOT = Path(__file__).resolve().parents[2]
APP = "sha256:" + "a" * 64
INGESTION = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64


def test_release_requires_matching_package_and_api_versions() -> None:
    assert (
        release_version("v1.2.3", '[project]\nversion="1.2.3"', 'app = FastAPI(version="1.2.3")')
        == "1.2.3"
    )
    for metadata, application in [
        ('[project]\nversion="1.2.4"', 'FastAPI(version="1.2.3")'),
        ('[project]\nversion="1.2.3"', 'FastAPI(version="1.2.4")'),
        ('[project]\nversion="1.2.3"', '# version="1.2.3"'),
    ]:
        with pytest.raises(ValueError):
            release_version("v1.2.3", metadata, application)


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v01.2.3", "v1.2.3-rc1", "v1.2.3\nx=y"])
def test_rejects_non_release_tags(tag: str) -> None:
    with pytest.raises(ValueError):
        release_version(tag, '[project]\nversion="1.2.3"', 'FastAPI(version="1.2.3")')


def test_compose_binds_both_published_digests_and_ingestion_configuration() -> None:
    template = (ROOT / "deploy/compose.yaml").read_text()
    rendered = render_compose(template, "1.2.3", APP, INGESTION, CONFIG)
    before, after = yaml.safe_load(template), yaml.safe_load(rendered)
    for name, service in after["services"].items():
        if "quay.io/bdobrica/kitchensoup:" in service["image"]:
            if name == "soup-ingest":
                assert service["image"].endswith(":soup-ingest-1.2.3@" + INGESTION)
                assert service["environment"]["SOUP_IMAGE_ID"] == CONFIG
            else:
                assert service["image"].endswith(":1.2.3@" + APP)
        service["image"] = before["services"][name]["image"]
    after["services"]["soup-ingest"]["environment"]["SOUP_IMAGE_ID"] = before["services"][
        "soup-ingest"
    ]["environment"]["SOUP_IMAGE_ID"]
    after["x-app"]["image"] = before["x-app"]["image"]
    assert after == before  # Network, startup ordering, secrets and limits are preserved.


@pytest.mark.parametrize("bad", ["latest", "sha256:abc", "sha256:" + "A" * 64, APP + "\nx=y"])
def test_compose_rejects_invalid_or_injectable_image_identities(bad: str) -> None:
    template = (ROOT / "deploy/compose.yaml").read_text()
    for identities in [(bad, INGESTION, CONFIG), (APP, bad, CONFIG), (APP, INGESTION, bad)]:
        with pytest.raises(ValueError):
            render_compose(template, "1.2.3", *identities)


def test_compose_renderer_fails_closed_when_template_structure_changes() -> None:
    template = (ROOT / "deploy/compose.yaml").read_text()
    for changed in [
        template.replace("SOUP_IMAGE_ID:", "UNKNOWN_IMAGE_ID:"),
        template.replace("image: quay.io/", "image: other.io/"),
    ]:
        with pytest.raises(ValueError):
            render_compose(changed, "1.2.3", APP, INGESTION, CONFIG)
