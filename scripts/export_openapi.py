"""Regenerate API contracts and the curated catalog schema from source models."""

import json
from pathlib import Path

from app.config import Settings
from app.ingestion.conversations import CanonicalConversation
from app.ingestion.documents import DocumentIngestionManifest, SoupResult
from app.ingestion.versions import DatasetManifest, TrainingExample
from app.main import create_app
from app.registry.schemas import Catalog
from app.training.schemas import AppSpec, Recipe, ResolvedPlan

schema = create_app(Settings(_env_file=None, app_name="KitchenSoup")).openapi()
destination = Path("docs/contracts/artifacts-v1.openapi.json")
destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
Path("docs/contracts/model-catalog-v1.schema.json").write_text(
    json.dumps(Catalog.model_json_schema(), indent=2, sort_keys=True) + "\n"
)

Path("docs/contracts/conversation-v1.schema.json").write_text(
    json.dumps(CanonicalConversation.model_json_schema(), indent=2, sort_keys=True) + "\n"
)


Path("docs/contracts/document-ingestion-v1.schema.json").write_text(
    json.dumps(DocumentIngestionManifest.model_json_schema(), indent=2, sort_keys=True) + "\n"
)
Path("docs/contracts/soup-ingest-response-v1.schema.json").write_text(
    json.dumps(SoupResult.model_json_schema(), indent=2, sort_keys=True) + "\n"
)

Path("docs/contracts/dataset-manifest-v1.schema.json").write_text(
    json.dumps(DatasetManifest.model_json_schema(), indent=2, sort_keys=True) + "\n"
)
Path("docs/contracts/training-example-v1.schema.json").write_text(
    json.dumps(TrainingExample.model_json_schema(), indent=2, sort_keys=True) + "\n"
)

for filename, model in (
    ("appspec-v1", AppSpec),
    ("recipe-v1", Recipe),
    ("resolved-plan-v1", ResolvedPlan),
):
    Path(f"docs/contracts/{filename}.schema.json").write_text(
        json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
    )
