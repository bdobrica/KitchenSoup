"""Packaged, version-pinned recipes; no external or executable recipe content."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from app.ingestion.sources import SourceError
from app.training.schemas import AppSpec, Recipe, TrainingParameters


def canonical_bytes(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: BaseModel) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_recipes() -> list[Recipe]:
    recipes = [
        Recipe.model_validate_json(path.read_bytes())
        for path in sorted(Path(__file__).with_name("recipes").glob("*.json"))
    ]
    keys = [(recipe.id, recipe.version) for recipe in recipes]
    if len(keys) != len(set(keys)) or not recipes:
        raise ValueError("Packaged recipes must have unique IDs/versions and cannot be empty")
    return recipes


DEFAULTS = {
    "conversation_imitation": ("conversation-sft-default", 1),
    "task_from_examples": ("task-sft-default", 1),
    "learn_from_documents": ("document-adaptation-default", 1),
}


def resolve_recipe(spec: AppSpec) -> tuple[Recipe, TrainingParameters]:
    key = (spec.recipe.id, spec.recipe.version) if spec.recipe else DEFAULTS[spec.intent]
    recipe = next((r for r in load_recipes() if (r.id, r.version) == key), None)
    if recipe is None:
        raise SourceError(422, "Unknown recipe or recipe version")
    if spec.intent not in (recipe.intent, "advanced"):
        raise SourceError(422, "Recipe does not match the selected intent")
    parameters = TrainingParameters.model_validate(
        recipe.defaults.model_dump() | spec.overrides.model_dump(exclude_none=True)
    )
    return recipe, parameters
