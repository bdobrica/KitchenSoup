import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion.conversations import (
    CanonicalConversation,
    CanonicalMessage,
    ConversationMetadata,
    TextPart,
)
from app.ingestion.versions import (
    DatasetManifest,
    DatasetVersionCreate,
    TrainingExample,
    conversation_examples,
)


def canonical(roles: list[tuple[str, str]]) -> CanonicalConversation:
    return CanonicalConversation(
        schema_name="kitchensoup.conversation/v1",
        conversation_id="fixture",
        title="Synthetic",
        source="fixture",
        metadata=ConversationMetadata(importer_version="1", warnings=[]),
        messages=[
            CanonicalMessage.model_validate(
                {
                    "message_id": str(index),
                    "role": role,
                    "content": [{"type": "text", "text": text}],
                }
            )
            for index, (role, text) in enumerate(roles)
        ],
    )


def test_assistant_targets_have_only_prior_context_and_keep_parts() -> None:
    source = canonical(
        [
            ("system", "Rules"),
            ("user", "First"),
            ("assistant", "Answer"),
            ("user", "Second"),
            ("assistant", "Final"),
        ]
    )
    source.messages[1].content.append(TextPart(text="More context"))
    result, messages, ignored = conversation_examples(source, uuid4(), uuid4())
    examples = list(result)
    assert (len(examples), messages, ignored) == (2, 5, 0)
    assert [m.content for m in examples[0].messages] == ["Rules", "First\nMore context", "Answer"]
    assert examples[1].messages[-1].content == "Final"
    assert examples[0].target_message_id == "2"


def test_empty_and_unprompted_assistant_targets_are_counted() -> None:
    result, messages, ignored = conversation_examples(
        canonical([("assistant", "Orphan"), ("user", " "), ("system", "Rules")]), uuid4(), uuid4()
    )
    assert list(result) == []
    assert (messages, ignored) == (2, 2)
    assert DatasetVersionCreate().conversation_ids is None
    assert DatasetVersionCreate(conversation_ids=[]).conversation_ids == []


@pytest.mark.parametrize(
    ("name", "model"),
    [("dataset-manifest", DatasetManifest), ("training-example", TrainingExample)],
)
def test_generated_version_contracts(
    name: str, model: type[DatasetManifest] | type[TrainingExample]
) -> None:
    assert (
        json.loads(Path(f"docs/contracts/{name}-v1.schema.json").read_text())
        == model.model_json_schema()
    )
