"""Bounded example preparation; the tokenizer is an explicit image-owned port."""

from typing import Protocol

from app.ingestion.versions import TrainingExample
from app.training.soup import RunnerError


class TrainingTokenizer(Protocol):
    def chat(self, messages: list[dict[str, str]], generation_prompt: bool) -> list[int]: ...
    def text(self, content: str) -> list[int]: ...


def tokenize_example(
    example: TrainingExample,
    kind: str,
    tokenizer: TrainingTokenizer,
    maximum: int,
) -> dict[str, list[int]]:
    if example.kind != kind:
        raise RunnerError("Example kind does not match the recipe")
    if kind == "conversation":
        if (
            len(example.messages) < 2
            or example.messages[-1].role != "assistant"
            or not example.messages[-1].content.strip()
            or not any(message.role == "user" for message in example.messages[:-1])
        ):
            raise RunnerError("Conversation has no final assistant target after a user input")
        messages = [message.model_dump() for message in example.messages]
        prefix = tokenizer.chat(messages[:-1], True)
        tokens = tokenizer.chat(messages, False)
        if not prefix or tokens[: len(prefix)] != prefix or len(tokens) <= len(prefix):
            raise RunnerError(
                "Chat template does not provide a stable final-assistant token boundary"
            )
        labels = [-100] * len(prefix) + tokens[len(prefix) :]
    else:
        if not example.text or not example.text.strip():
            raise RunnerError("Document example is empty")
        tokens = tokenizer.text(example.text)
        labels = tokens.copy()
    if len(tokens) > maximum:
        raise RunnerError("Example exceeds the reviewed sequence limit; no truncation was applied")
    if len(tokens) < 2 or not any(token != -100 for token in labels[1:]):
        raise RunnerError("Example has no causal loss target")
    return {"input_ids": tokens, "attention_mask": [1] * len(tokens), "labels": labels}
