"""Regenerate API contracts and the curated catalog schema from source models."""

import json
from pathlib import Path

from app.config import Settings
from app.ingestion.conversations import CanonicalConversation
from app.main import create_app
from app.registry.schemas import Catalog

schema = create_app(Settings(_env_file=None, app_name="KitchenSoup")).openapi()
destination = Path("docs/contracts/artifacts-v1.openapi.json")
destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
Path("docs/contracts/model-catalog-v1.schema.json").write_text(
    json.dumps(Catalog.model_json_schema(), indent=2, sort_keys=True) + "\n"
)

Path("docs/contracts/conversation-v1.schema.json").write_text(
    json.dumps(CanonicalConversation.model_json_schema(), indent=2, sort_keys=True) + "\n"
)
