import json
import struct
from io import BytesIO
from zipfile import ZipFile

import pytest


@pytest.fixture
def model_archive() -> bytes:
    """A structural fixture, not a runnable pretrained model."""
    header = json.dumps(
        {"test.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    ).encode()
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr(
            "model/config.json",
            json.dumps({"model_type": "qwen2", "architectures": ["Qwen2ForCausalLM"]}),
        )
        archive.writestr("model/tokenizer_config.json", "{}")
        archive.writestr("model/tokenizer.json", '{"model": {"type": "BPE", "vocab": {}}}')
        archive.writestr(
            "model/model.safetensors", struct.pack("<Q", len(header)) + header + b"\0" * 4
        )
    return result.getvalue()
