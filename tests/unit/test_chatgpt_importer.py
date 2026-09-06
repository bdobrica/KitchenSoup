import json
from pathlib import Path
from typing import Any

import pytest

from app.ingestion.chatgpt import ChatGPTImporter
from app.ingestion.conversations import CanonicalConversation
from app.ingestion.sources import SourceError

FIXTURE = Path("tests/fixtures/chatgpt/conversations.json")


def parse(tmp_path: Path, data: Any) -> list[CanonicalConversation]:
    file = tmp_path / "conversations.json"
    file.write_text(json.dumps(data))
    importer = ChatGPTImporter()
    return list(importer.parse(importer.detect(tmp_path)))


def test_active_branch_and_text_normalization(tmp_path: Path) -> None:
    result = parse(tmp_path, json.loads(FIXTURE.read_text()))
    assert [item.conversation_id for item in result] == ["synthetic-chat-001", "synthetic-chat-002"]
    first = result[0]
    assert [message.message_id for message in first.messages] == ["message-q", "message-a"]
    assert first.messages[0].content[0].text == "Hello\nworld"
    assert first.metadata.warnings == ["alternate_branches_ignored"]
    assert first.metadata.source_created_at is not None
    second = result[1]
    assert len(second.messages) == 2
    assert set(second.metadata.warnings) == {
        "multimodal_ignored",
        "tool_content_ignored",
        "attachments_ignored",
    }
    assert second.metadata.source_created_at is None
    assert '"schema":"kitchensoup.conversation/v1"' in first.model_dump_json(by_alias=True)


def test_nested_shards_and_wrapper(tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_text())
    root = tmp_path / "export"
    root.mkdir()
    (root / "conversations-1.json").write_text(json.dumps({"conversations": [data[0]]}))
    (root / "conversations-2.json").write_text(json.dumps([data[1]]))
    (root / "account.json").write_text("{}")
    importer = ChatGPTImporter()
    assert len(list(importer.parse(importer.detect(tmp_path)))) == 2
    (root / "conversations.json").write_text("[]")
    with pytest.raises(SourceError, match="ambiguous"):
        importer.detect(tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "cycle",
        "missing_parent",
        "missing_current",
        "ambiguous_branch",
        "duplicate_id",
        "bad_date",
        "bad_role_shape",
    ],
)
def test_malformed_graphs_and_metadata(tmp_path: Path, change: str) -> None:
    data = json.loads(FIXTURE.read_text())
    row = data[0]
    if change == "cycle":
        row["mapping"]["question"]["parent"] = "answer"
    elif change == "missing_parent":
        row["mapping"]["answer"]["parent"] = "absent"
    elif change == "missing_current":
        row["current_node"] = "absent"
    elif change == "ambiguous_branch":
        row.pop("current_node")
    elif change == "duplicate_id":
        data.append(data[0])
    elif change == "bad_date":
        row["create_time"] = "yesterday"
    else:
        row["mapping"]["answer"]["message"]["author"]["role"] = {"unknown": True}
        result = parse(tmp_path, data)
        assert "unknown_role_ignored" in result[0].metadata.warnings
        return
    with pytest.raises(SourceError):
        parse(tmp_path, data)


def test_missing_current_single_branch_and_role_flags(tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_text())[1:]
    data[0].pop("current_node")
    mapping = data[0]["mapping"]
    mapping["final"]["message"]["author"]["role"] = "developer"
    mapping["tool"]["message"]["metadata"] = {"is_visually_hidden_from_conversation": True}
    result = parse(tmp_path, data)[0]
    assert result.messages[-1].role == "system"
    assert {"developer_role_normalized", "hidden_messages_ignored"} <= set(result.metadata.warnings)


def test_malformed_json_limits_and_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = ChatGPTImporter()
    file = tmp_path / "conversations.json"
    file.write_text('[{"id":"one","id":"two"}]')
    with pytest.raises(SourceError):
        list(importer.parse([file]))
    file.write_text("[")
    with pytest.raises(SourceError):
        list(importer.parse([file]))
    file.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setattr("app.ingestion.chatgpt.MAX_JSON_BYTES", 1)
    with pytest.raises(SourceError, match="64 MiB"):
        list(importer.parse([file]))
    published = json.loads(Path("docs/contracts/conversation-v1.schema.json").read_text())
    assert published == CanonicalConversation.model_json_schema()
