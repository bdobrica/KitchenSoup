"""Bounded ZIP inspection without extraction, checkpoint loading, or remote code."""

import json
import math
import stat
import struct
import time
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any, BinaryIO


class RegistryError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def invalid(message: str) -> RegistryError:
    return RegistryError(422, message)


def json_object(data: bytes) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON keys")
        return result

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise invalid("A model JSON file is malformed") from None
    if not isinstance(result, dict):
        raise invalid("Model JSON files must contain objects")
    return result


def validate_structure(names: set[str], config: dict[str, Any]) -> None:
    supported = {
        "qwen2": "Qwen2ForCausalLM",
        "llama": "LlamaForCausalLM",
        "mistral": "MistralForCausalLM",
    }
    model_type = config.get("model_type")
    if (
        not isinstance(model_type, str)
        or model_type not in supported
        or config.get("architectures") != [supported.get(model_type)]
    ):
        raise invalid("Use a native Qwen2, Llama, or Mistral causal language model")
    if config.get("auto_map") or config.get("quantization_config"):
        raise invalid("Custom model code and pre-quantized checkpoints are not supported yet")
    if "tokenizer_config.json" not in names or not (
        "tokenizer.json" in names
        or "tokenizer.model" in names
        or {"vocab.json", "merges.txt"} <= names
    ):
        raise invalid("Include tokenizer_config.json and tokenizer files")
    if not any(name.endswith(".safetensors") for name in names):
        raise invalid(
            "Include full model weights in safetensors format; adapters alone are unsupported"
        )


def validate_tensors(header: dict[str, Any], data_size: int) -> set[str]:
    widths = {
        "BOOL": 1,
        "U8": 1,
        "I8": 1,
        "I16": 2,
        "U16": 2,
        "F16": 2,
        "BF16": 2,
        "I32": 4,
        "U32": 4,
        "F32": 4,
        "I64": 8,
        "U64": 8,
        "F64": 8,
    }
    ranges = []
    names = set()
    for name, tensor in header.items():
        if name == "__metadata__":
            if not isinstance(tensor, dict) or not all(isinstance(v, str) for v in tensor.values()):
                raise invalid("Invalid safetensors metadata")
            continue
        if not isinstance(tensor, dict):
            raise invalid("Invalid safetensors header")
        shape, offsets, dtype = tensor.get("shape"), tensor.get("data_offsets"), tensor.get("dtype")
        if (
            not isinstance(shape, list)
            or len(shape) > 16
            or not all(type(n) is int and 0 <= n <= 2**40 for n in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(type(n) is int for n in offsets)
            or not isinstance(dtype, str)
            or dtype not in widths
        ):
            raise invalid("Unsupported or malformed safetensors tensor")
        start, end = offsets
        if (
            start < 0
            or end < start
            or end > data_size
            or end - start != math.prod(shape) * widths[dtype]
        ):
            raise invalid("Safetensors dimensions do not match the stored data")
        ranges.append((start, end))
        names.add(name)
    cursor = 0
    for start, end in sorted(ranges):
        if start != cursor:
            raise invalid("Safetensors data has gaps or overlapping ranges")
        cursor = end
    if not names or cursor != data_size:
        raise invalid("Safetensors file contains no valid complete tensor data")
    return names


def inspect_archive(source: BinaryIO) -> None:
    try:
        _inspect_zip(source)
    except (
        zipfile.BadZipFile,
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        struct.error,
        ValueError,
        zlib.error,
    ):
        raise invalid("Upload a valid, unencrypted ZIP containing a Transformers model") from None


def _inspect_zip(source: BinaryIO) -> None:
    started = time.monotonic()
    source.seek(0, 2)
    length = source.tell()
    source.seek(max(0, length - 65557))
    tail = source.read(65557)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or len(tail) - marker < 22:
        raise invalid("Upload a ZIP archive; other archive formats are not supported yet")
    _, disk, directory_disk, disk_count, count, directory_size, offset, comment = struct.unpack(
        "<4s4H2LH", tail[marker : marker + 22]
    )
    if (
        disk
        or directory_disk
        or disk_count != count
        or count > 4096
        or directory_size > 4 * 1024**2
        or offset == 0xFFFFFFFF
        or offset + directory_size > length
        or marker + 22 + comment != len(tail)
    ):
        raise invalid(
            "Archive directory exceeds limits or uses unsupported ZIP64/multi-volume metadata"
        )
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) != count:
            raise invalid("Inconsistent ZIP directory")
        files = {}
        seen = set()
        total = 0
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (
                not name
                or name != entry.orig_filename
                or "\\" in name
                or ":" in name
                or path.is_absolute()
                or any(p in {"", ".", ".."} for p in name.rstrip("/").split("/"))
                or any(ord(c) < 32 for c in name)
                or len(name) > 512
                or name.casefold().rstrip("/") in seen
            ):
                raise invalid("Archive contains unsafe or duplicate paths")
            seen.add(name.casefold().rstrip("/"))
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise invalid("Archive links, special files, and encryption are unsupported")
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise invalid("Use ZIP stored or deflate compression")
            if entry.is_dir():
                continue
            if path.suffix.lower() in {
                ".py",
                ".bin",
                ".pt",
                ".pth",
                ".pkl",
                ".pickle",
                ".exe",
                ".so",
            }:
                raise invalid(
                    "Use safetensors weights without executable code or pickle checkpoints"
                )
            total += entry.file_size
            if total > 8 * 1024**3 or entry.file_size > max(1, entry.compress_size) * 100:
                raise invalid("Archive exceeds the 8 GiB expanded size or 100:1 compression limit")
            files[name] = entry
        configs = [name for name in files if PurePosixPath(name).name == "config.json"]
        if len(configs) != 1:
            raise invalid("Include exactly one model config.json")
        root = configs[0][: -len("config.json")]
        if any(not name.startswith(root) for name in files):
            raise invalid("Keep all model files in one directory")
        contents: dict[str, dict[str, Any]] = {}
        tensors: dict[str, set[str]] = {}
        metadata_bytes = 0
        for path_name, entry in files.items():
            name = path_name[len(root) :]
            if time.monotonic() - started > 60:
                raise invalid("Archive inspection exceeded its time limit")
            with archive.open(entry) as body:
                if name.endswith(".safetensors"):
                    header_size = int.from_bytes(body.read(8), "little")
                    if not 2 <= header_size <= 1024**2 or 8 + header_size > entry.file_size:
                        raise invalid("Invalid or oversized safetensors header")
                    metadata_bytes += header_size
                    if metadata_bytes > 32 * 1024**2:
                        raise invalid("Archive model metadata exceeds 32 MiB")
                    header = body.read(header_size)
                    if not header.startswith(b"{"):
                        raise invalid("Safetensors header must begin with a JSON object")
                    tensors[name] = validate_tensors(
                        json_object(header), entry.file_size - 8 - header_size
                    )
                    read = 8 + header_size
                elif name.endswith(".json"):
                    if entry.file_size > 16 * 1024**2:
                        raise invalid("Model JSON files must be at most 16 MiB")
                    metadata_bytes += entry.file_size
                    if metadata_bytes > 32 * 1024**2:
                        raise invalid("Archive model metadata exceeds 32 MiB")
                    contents[name] = json_object(body.read(16 * 1024**2 + 1))
                    read = entry.file_size
                else:
                    read = 0
                while chunk := body.read(1024**2):
                    read += len(chunk)
                    if read > entry.file_size or time.monotonic() - started > 60:
                        raise invalid("Archive inspection exceeded its resource limits")
        names = {name[len(root) :] for name in files}
        validate_structure(names, contents["config.json"])
        if contents["tokenizer_config.json"].get("auto_map"):
            raise invalid("Custom tokenizer code is unsupported")
        if "tokenizer.json" in contents and not isinstance(
            contents["tokenizer.json"].get("model"), dict
        ):
            raise invalid("tokenizer.json must contain a tokenizer model")
        index = contents.get("model.safetensors.index.json")
        if index is not None:
            weights = index.get("weight_map")
            if not isinstance(weights, dict) or not weights:
                raise invalid("Invalid safetensors shard index")
            for tensor, filename in weights.items():
                if not isinstance(filename, str) or tensor not in tensors.get(filename, set()):
                    raise invalid("Safetensors index references a missing shard or tensor")
        elif "model.safetensors" not in tensors:
            raise invalid("Include model.safetensors or a complete model.safetensors.index.json")
