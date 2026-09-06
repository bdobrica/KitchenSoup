import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

destination = Path("docs/contracts/artifacts-v1.openapi.json")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    json.dumps(create_app(Settings(_env_file=None)).openapi(), indent=2, sort_keys=True) + "\n"
)
