"""One bounded offline generation; ML libraries exist only in the pinned image."""

import hashlib
import importlib
import json
from pathlib import Path

from app.evaluation.schemas import InferenceInput, InferenceOutput
from app.training.runner import file_record, read_bounded, verify_bundle
from app.training.soup import RunnerError, RunnerInput


def verify_input(root: Path) -> tuple[InferenceInput, RunnerInput, str]:
    payload = read_bounded(root / "inference.json", 1024**2)
    request = InferenceInput.model_validate_json(payload)
    run_bytes = read_bounded(root / "run.json", 8 * 1024**2)
    if hashlib.sha256(run_bytes).hexdigest() != request.run_sha256:
        raise RunnerError("Inference training input hash mismatch")
    run = RunnerInput.model_validate_json(run_bytes)
    verify_bundle(root, run)
    adapter = root / "adapter"
    if request.adapter_files:
        if adapter.is_symlink() or not adapter.is_dir():
            raise RunnerError("Missing adapter")
        actual = []
        for path in sorted(adapter.rglob("*")):
            if path.is_symlink():
                raise RunnerError("Adapter links are unsupported")
            if path.is_dir():
                continue
            if path.suffix not in {
                ".json",
                ".safetensors",
                ".txt",
                ".model",
                ".jinja",
                ".tiktoken",
            }:
                raise RunnerError("Unsupported adapter file")
            actual.append(file_record(adapter, path, 8 * 1024**3))
        if actual != sorted(request.adapter_files, key=lambda item: item.path):
            raise RunnerError("Adapter inventory mismatch")
        config = json.loads(read_bounded(adapter / "adapter_config.json", 1024**2))
        if config.get("peft_type") != "LORA" or config.get("task_type") != "CAUSAL_LM":
            raise RunnerError("Only causal LoRA adapters are supported")
        if config.get("auto_mapping") or config.get("loftq_config"):
            raise RunnerError("Custom adapter loading is unsupported")
    elif adapter.exists() and (adapter.is_symlink() or any(adapter.iterdir())):
        raise RunnerError("Uninventoried adapter")
    return request, run, hashlib.sha256(payload).hexdigest()


def generate(root: Path, output: Path) -> None:
    request, run, identity = verify_input(root)
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    if not torch.cuda.is_available():
        raise RunnerError("Evaluation requires a CUDA GPU")
    precision = run.plan.resolved.training.precision
    if precision == "bf16" and not torch.cuda.is_bf16_supported(including_emulation=False):
        raise RunnerError("BF16 evaluation requires native BF16; use an FP16 training plan")
    dtype = torch.bfloat16 if precision == "bf16" else torch.float16
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        str(root / "model"), local_files_only=True, trust_remote_code=False
    )
    model = transformers.AutoModelForCausalLM.from_pretrained(
        str(root / "model"),
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        torch_dtype=dtype,
    ).to("cuda")
    if request.adapter_files:
        peft = importlib.import_module("peft")
        model = peft.PeftModel.from_pretrained(
            model,
            str(root / "adapter"),
            local_files_only=True,
            is_trainable=False,
            autocast_adapter_dtype=False,
        )
    model.eval()
    text = tokenizer.apply_chat_template(
        [{"role": "user", "content": request.prompt}], tokenize=False, add_generation_prompt=True
    )
    tokens = tokenizer(text, return_tensors="pt", add_special_tokens=False).to("cuda")
    length = tokens["input_ids"].shape[1]
    if length + request.max_new_tokens > run.plan.resolved.training.max_sequence_length:
        raise RunnerError("Evaluation prompt exceeds the reviewed context limit")
    with torch.inference_mode():
        result = model.generate(
            **tokens,
            max_new_tokens=request.max_new_tokens,
            do_sample=False,
            num_beams=1,
            pad_token_id=tokenizer.eos_token_id,
        )
    response = tokenizer.decode(result[0][length:], skip_special_tokens=True)
    answer = InferenceOutput(input_sha256=identity, response=response)
    (output / "response.json").write_text(answer.model_dump_json(by_alias=True))


def main() -> None:
    try:
        generate(Path("/input"), Path("/output"))
    except Exception:
        # Neither prompts, generated content nor third-party exception payloads enter logs.
        raise SystemExit(
            "Local evaluation failed; verify model, context and GPU capacity"
        ) from None


if __name__ == "__main__":
    main()
