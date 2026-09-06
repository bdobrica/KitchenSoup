import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ingestion.sources import SourceError
from app.training.recipes import digest, load_recipes, resolve_recipe
from app.training.schemas import AppSpec, Recipe, RecipeRef, ResolvedPlan, TrainingOverrides


def spec(**values: object) -> AppSpec:
    return AppSpec.model_validate(
        {
            "intent": "conversation_imitation",
            "base_model_version_id": uuid4(),
            "dataset_version_id": uuid4(),
            **values,
        }
    )


@pytest.mark.parametrize(
    "intent,recipe_id,epochs,rate",
    [
        ("conversation_imitation", "conversation-sft-default", 3, 0.0002),
        ("task_from_examples", "task-sft-default", 3, 0.0001),
        ("learn_from_documents", "document-adaptation-default", 1, 0.00005),
    ],
)
def test_intents_resolve_to_pinned_concrete_defaults(
    intent: str,
    recipe_id: str,
    epochs: int,
    rate: float,
) -> None:
    recipe, training = resolve_recipe(spec(intent=intent))
    assert (recipe.id, recipe.version) == (recipe_id, 1)
    assert (training.epochs, training.learning_rate) == (epochs, rate)
    assert training.batch_size == 1 and training.gradient_accumulation_steps == 16
    assert recipe.overlength == "reject"
    assert len(digest(recipe)) == 64


def test_appspec_round_trip_and_advanced_overrides_do_not_mutate_recipes() -> None:
    original = load_recipes()
    body = spec(
        intent="advanced",
        recipe=RecipeRef(id="task-sft-default", version=1),
        overrides=TrainingOverrides(epochs=2, learning_rate=0.0003),
    )
    assert AppSpec.model_validate_json(body.model_dump_json(by_alias=True)) == body
    _, training = resolve_recipe(body)
    assert (training.epochs, training.learning_rate) == (2, 0.0003)
    assert load_recipes() == original
    assert not {"engine", "soup", "generated_config", "executor"} & body.model_dump().keys()


@pytest.mark.parametrize(
    "values",
    [
        {"schema": "kitchensoup.appspec/v2"},
        {"intent": "unsupported"},
        {"intent": "advanced"},
        {"engine": {"command": "anything"}},
        {"executor": {"ref": "local"}},
        {"overrides": {"epochs": True}},
        {"overrides": {"epochs": 0}},
        {"overrides": {"learning_rate": float("nan")}},
        {"overrides": {"learning_rate": "0.001"}},
        {"overrides": {"batch_size": 65}},
        {"overrides": {"lora_dropout": 1.0}},
        {"overrides": {"shell": "anything"}},
        {"recipe": {"id": "../external", "version": 1}},
    ],
)
def test_invalid_or_future_requests_are_not_silently_migrated(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        spec(**values)


@pytest.mark.parametrize(
    "recipe_id,version",
    [("missing", 1), ("conversation-sft-default", 2), ("document-adaptation-default", 1)],
)
def test_unknown_or_incompatible_recipe_is_rejected(recipe_id: str, version: int) -> None:
    with pytest.raises(SourceError):
        resolve_recipe(spec(recipe={"id": recipe_id, "version": version}))


def test_invalid_recipe_modes_and_parameters_are_rejected() -> None:
    recipe = load_recipes()[0].model_dump(by_alias=True)
    for change in (
        {"objective": "all_text_tokens"},
        {"schema": "kitchensoup.recipe/v2"},
        {"command": "shell"},
        {"defaults": {"epochs": -1}},
    ):
        with pytest.raises(ValidationError):
            Recipe.model_validate(recipe | change)


@pytest.mark.parametrize(
    "name,model", [("appspec", AppSpec), ("recipe", Recipe), ("resolved-plan", ResolvedPlan)]
)
def test_generated_training_schemas(
    name: str, model: type[AppSpec | Recipe | ResolvedPlan]
) -> None:
    assert (
        json.loads(Path(f"docs/contracts/{name}-v1.schema.json").read_text())
        == model.model_json_schema()
    )
