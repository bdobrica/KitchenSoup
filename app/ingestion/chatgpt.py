"""Bounded ChatGPT mapping exports; parent chains define the selected branch."""

import json
import math
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingestion.conversations import (
    CanonicalConversation,
    CanonicalMessage,
    ConversationMetadata,
    TextPart,
    WarningCode,
)
from app.ingestion.sources import SourceError

MAX_JSON_BYTES = 64 * 1024**2
MAX_CONVERSATIONS = 10000
MAX_NODES = 20000
MAX_TOTAL_NODES = 100000


def invalid(message: str) -> SourceError:
    return SourceError(422, message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise invalid("Export JSON contains duplicate keys")
    return result


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2000:
        raise invalid("Conversation and message identifiers must be nonempty strings")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise invalid("Export contains invalid Unicode text") from None
    return value


class ChatGPTImporter:
    name = "chatgpt"
    version = "1"

    def detect(self, root: Path) -> list[Path]:
        paths = [
            path
            for path in root.rglob("*.json")
            if re.fullmatch(r"conversations(?:-\d+)?\.json", path.name)
        ]
        if not paths:
            raise invalid(
                "No supported conversations.json or conversations-N.json files found in the export"
            )
        if (
            len({path.parent for path in paths}) != 1
            or len(paths) > 1
            and any(path.name == "conversations.json" for path in paths)
        ):
            raise invalid("Export contains ambiguous conversation file layouts")
        return sorted(
            paths, key=lambda path: int(path.stem.split("-")[-1]) if "-" in path.stem else 0
        )

    def parse(self, paths: list[Path]) -> Iterator[CanonicalConversation]:
        started = time.monotonic()
        total_bytes = 0
        total_nodes = 0
        seen: set[str] = set()
        for path in paths:
            total_bytes += path.stat().st_size
            if total_bytes > MAX_JSON_BYTES:
                raise invalid("Conversation JSON exceeds the 64 MiB import limit")
            try:
                data = json.loads(
                    path.read_bytes().decode("utf-8-sig"), object_pairs_hook=unique_object
                )
            except (ValueError, UnicodeError, RecursionError):
                raise invalid("Conversation JSON is malformed") from None
            if isinstance(data, dict) and set(data) == {"conversations"}:
                data = data["conversations"]
            if not isinstance(data, list):
                raise invalid("Conversation JSON must be a list or a conversations wrapper")
            for raw in data:
                if time.monotonic() - started > 60:
                    raise invalid("Conversation parsing exceeded its time limit")
                if not isinstance(raw, dict) or not isinstance(raw.get("mapping"), dict):
                    raise invalid("Expected ChatGPT conversation mapping objects")
                total_nodes += len(raw["mapping"])
                if total_nodes > MAX_TOTAL_NODES or len(seen) >= MAX_CONVERSATIONS:
                    raise invalid("Export exceeds conversation or message limits")
                conversation = self._conversation(raw)
                if conversation.conversation_id in seen:
                    raise invalid("Export contains duplicate conversation IDs")
                seen.add(conversation.conversation_id)
                yield conversation
        if not seen:
            raise invalid("Export contains no conversations")

    def _conversation(self, raw: dict[str, Any]) -> CanonicalConversation:
        source_id = identifier(raw.get("conversation_id") or raw.get("id"))
        title = raw.get("title")
        if title is None or title == "":
            title = "Untitled conversation"
        if not isinstance(title, str) or len(title) > 4000:
            raise invalid("Conversation title is invalid or too long")
        try:
            title.encode("utf-8")
        except UnicodeError:
            raise invalid("Export contains invalid Unicode text") from None
        mapping = raw["mapping"]
        if not mapping or len(mapping) > MAX_NODES:
            raise invalid("Conversation mapping is empty or exceeds 20,000 nodes")
        for node_id, node in mapping.items():
            identifier(node_id)
            if not isinstance(node, dict):
                raise invalid("Malformed conversation node")
            parent = node.get("parent")
            if parent is not None and (not isinstance(parent, str) or parent not in mapping):
                raise invalid("Conversation contains a missing parent")
        # Validate all branches, including omitted branches, in linear time.
        visited: set[str] = set()
        for start in mapping:
            chain: set[str] = set()
            cursor = start
            while cursor is not None and cursor not in visited:
                if cursor in chain:
                    raise invalid("Conversation mapping contains a cycle")
                chain.add(cursor)
                cursor = mapping[cursor].get("parent")
            visited.update(chain)
        current = raw.get("current_node")
        if current is None:
            parents = {node.get("parent") for node in mapping.values()}
            leaves = set(mapping) - parents
            if len(leaves) != 1:
                raise invalid("Branched conversation is missing its current_node")
            current = next(iter(leaves))
        if not isinstance(current, str) or current not in mapping:
            raise invalid("Conversation current_node is missing from its mapping")
        path = []
        while current is not None:
            path.append(current)
            current = mapping[current].get("parent")
        warnings: set[WarningCode] = set()
        if len(path) != len(mapping):
            warnings.add("alternate_branches_ignored")
        messages = []
        message_ids = set()
        for node_id in reversed(path):
            message = mapping[node_id].get("message")
            if message is None:
                continue
            if not isinstance(message, dict):
                raise invalid("Malformed conversation message")
            metadata = message.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise invalid("Malformed message metadata")
            if metadata.get("attachments"):
                warnings.add("attachments_ignored")
            if metadata.get("is_visually_hidden_from_conversation"):
                warnings.add("hidden_messages_ignored")
                continue
            author = message.get("author")
            role = author.get("role") if isinstance(author, dict) else None
            if role in ("tool", "function") or message.get("recipient") not in (None, "all"):
                warnings.add("tool_content_ignored")
                continue
            if role == "developer":
                role = "system"
                warnings.add("developer_role_normalized")
            if role not in ("system", "user", "assistant"):
                warnings.add("unknown_role_ignored")
                continue
            content = message.get("content")
            if not isinstance(content, dict):
                raise invalid("Malformed message content")
            content_type = content.get("content_type")
            if content_type not in ("text", "multimodal_text"):
                warnings.add("unsupported_content_ignored")
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                raise invalid("Text messages must contain a parts list")
            texts = []
            for part in parts:
                if isinstance(part, str):
                    text = part.replace("\r\n", "\n").replace("\r", "\n")
                    try:
                        text.encode("utf-8")
                    except UnicodeError:
                        raise invalid("Export contains invalid Unicode text") from None
                    if text:
                        texts.append(TextPart(text=text))
                else:
                    warnings.add("multimodal_ignored")
            if content_type == "multimodal_text":
                warnings.add("multimodal_ignored")
            if not texts:
                continue
            message_id = identifier(message.get("id") or node_id)
            if message_id in message_ids:
                raise invalid("Conversation contains duplicate message IDs")
            message_ids.add(message_id)
            messages.append(CanonicalMessage(message_id=message_id, role=role, content=texts))
        created = raw.get("create_time")
        timestamp = None
        if created is not None:
            try:
                if type(created) not in (int, float) or not math.isfinite(created):
                    raise ValueError
                timestamp = datetime.fromtimestamp(created, UTC)
            except (ValueError, OverflowError, OSError):
                raise invalid("Conversation creation time is invalid") from None
        return CanonicalConversation(
            schema_name="kitchensoup.conversation/v1",
            source="chatgpt",
            conversation_id=source_id,
            title=title,
            messages=messages,
            metadata=ConversationMetadata(
                source_created_at=timestamp,
                importer_version=self.version,
                warnings=sorted(warnings),
            ),
        )
