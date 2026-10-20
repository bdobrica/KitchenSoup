"""Validate release versions and render immutable installation image identities."""

import argparse
import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "quay.io/bdobrica/kitchensoup"
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
DIGEST = r"sha256:[0-9a-f]{64}"


def release_version(tag: str, metadata: str, application: str) -> str:
    if not re.fullmatch("v" + VERSION, tag):
        raise ValueError("Release tags must have the form vX.Y.Z (stable releases only)")
    version = tag[1:]
    if tomllib.loads(metadata)["project"]["version"] != version:
        raise ValueError("Git tag and package version differ")
    api_versions = [
        keyword.value.value
        for node in ast.walk(ast.parse(application))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FastAPI"
        for keyword in node.keywords
        if keyword.arg == "version" and isinstance(keyword.value, ast.Constant)
    ]
    if api_versions != [version]:
        raise ValueError("Git tag and application API version differ")
    return version


def render_compose(
    template: str, version: str, app_digest: str, ingestion_digest: str, ingestion_id: str
) -> str:
    if not re.fullmatch(VERSION, version):
        raise ValueError("Invalid release version")
    if not all(re.fullmatch(DIGEST, d) for d in (app_digest, ingestion_digest, ingestion_id)):
        raise ValueError("Expected SHA-256 image identities")
    for tag, digest, expected in [
        (version, app_digest, 4),
        ("soup-ingest-" + version, ingestion_digest, 1),
    ]:
        old_tag = r"[0-9]+\.[0-9]+\.[0-9]+"
        if tag.startswith("soup-ingest-"):
            old_tag = "soup-ingest-" + old_tag
        pattern = rf"(?m)^( +image: ){re.escape(IMAGE)}:{old_tag}@{DIGEST}$"
        template, count = re.subn(pattern, rf"\g<1>{IMAGE}:{tag}@{digest}", template)
        if count != expected:
            raise ValueError("Unexpected installation image references; review release rendering")
    template, count = re.subn(
        rf"(?m)^( +SOUP_IMAGE_ID: ){DIGEST}$", rf"\g<1>{ingestion_id}", template
    )
    if count != 1:
        raise ValueError("Expected exactly one ingestion configuration identity")
    return template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    version_parser = commands.add_parser("version")
    version_parser.add_argument("tag")
    compose_parser = commands.add_parser("compose")
    for name in ("version", "app_digest", "ingestion_digest", "ingestion_id", "output"):
        compose_parser.add_argument(name)
    args = parser.parse_args()
    if args.command == "version":
        version = release_version(
            args.tag, (ROOT / "pyproject.toml").read_text(), (ROOT / "app/main.py").read_text()
        )
        print(f"version={version}")
    else:
        result = render_compose(
            (ROOT / "deploy/compose.yaml").read_text(),
            args.version,
            args.app_digest,
            args.ingestion_digest,
            args.ingestion_id,
        )
        Path(args.output).write_text(result)


if __name__ == "__main__":
    main()
