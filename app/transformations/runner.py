"""Offline merge/AWQ runner. ML imports never enter the application process."""

import hashlib
import importlib
import json
import shutil
from pathlib import Path

from app.training.runner import file_record, read_bounded
from app.transformations.schemas import TransformSpec

SUFFIXES = {".json", ".safetensors", ".txt", ".model", ".tiktoken", ".jinja"}


def run(root: Path, output: Path) -> None:
    payload = read_bounded(root / "transform.json", 1024**2)
    spec = TransformSpec.model_validate_json(payload)
    inventory = json.loads(read_bounded(root / "inventory.json", 4 * 1024**2))
    actual = []
    for folder in ("model", "adapter"):
        directory = root / folder
        if directory.is_symlink():
            raise ValueError("Linked input")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                raise ValueError("Non-regular input")
            if path.is_file():
                if path.suffix not in SUFFIXES:
                    raise ValueError("Unsupported input")
                actual.append(file_record(root, path).model_dump(mode="json"))
    if actual != inventory:
        raise ValueError("Input inventory mismatch")
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    if not torch.cuda.is_available():
        raise ValueError("CUDA required")
    if spec.precision == "bf16" and not torch.cuda.is_bf16_supported(including_emulation=False):
        raise ValueError("Native BF16 required")
    dtype = torch.bfloat16 if spec.precision == "bf16" else torch.float16
    model_path = str(root / "model")
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    destination = output / "model"
    destination.mkdir()
    if spec.operation == "merge":
        model = transformers.AutoModelForCausalLM.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
            torch_dtype=dtype,
            device_map="cuda",
        )
        peft = importlib.import_module("peft")
        model = peft.PeftModel.from_pretrained(
            model,
            str(root / "adapter"),
            local_files_only=True,
            is_trainable=False,
            autocast_adapter_dtype=False,
        ).merge_and_unload(safe_merge=True)
        model.save_pretrained(destination, safe_serialization=True, max_shard_size="2GB")
    else:
        awq = importlib.import_module("awq")
        model = awq.AutoAWQForCausalLM.from_pretrained(
            model_path,
            trust_remote_code=False,
            safetensors=True,
            torch_dtype=torch.float16,
            device_map="cpu",
        )
        model.quantize(
            tokenizer,
            quant_config={"zero_point": True, "q_group_size": 128, "w_bit": 4, "version": "GEMM"},
            calib_data=spec.calibration,
            max_calib_samples=len(spec.calibration),
            max_calib_seq_len=128,
            n_parallel_calib_samples=1,
        )
        model.save_quantized(str(destination), safetensors=True, shard_size="2GB")
    tokenizer.save_pretrained(destination)
    for path in (root / "model").iterdir():
        if path.suffix in {".jinja", ".txt"} and not (destination / path.name).exists():
            shutil.copyfile(path, destination / path.name)
    records = [file_record(destination, p) for p in sorted(destination.rglob("*")) if p.is_file()]
    if not records or sum(item.size_bytes for item in records) > 8 * 1024**3:
        raise ValueError("Output limit exceeded")
    (output / "result.json").write_text(
        json.dumps(
            {
                "input_sha256": hashlib.sha256(payload).hexdigest(),
                "files": [r.model_dump(mode="json") for r in records],
            }
        )
    )


def main() -> None:
    try:
        run(Path("/input"), Path("/output"))
    except Exception:
        raise SystemExit(
            "Transformation failed; verify model, calibration and GPU capacity"
        ) from None


if __name__ == "__main__":
    main()
