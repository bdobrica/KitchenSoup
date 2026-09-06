# Soup trainer image and translation

`make build-soup` builds `kitchensoup-soup-trainer:local` separately from the web and
ingestion images. It pins Python 3.12 by base digest, Soup 0.74.0, PyTorch 2.8.0,
Transformers 5.16.1, PEFT 0.20.0, TRL 0.29.0 and all transitive packages. CUDA wheels
make this a large download/build. No training dependency is added to the web app.

This document describes the standalone runner. The [local Docker executor](local-training.md)
materializes registered inputs and submits TrainingRuns from an immutable
[review plan](training-plans.md). Adapter registration remains later work.

## Runner input

The [training input schema](contracts/training-input-v1.schema.json) describes:

- `schema`: `kitchensoup.training-input/v1`;
- `run_id`: stable attempt/run UUID;
- `plan`: reviewed `appspec`, `resolved`, `sha256` (the PlanPreview shape);
- `model_source`: exact copy of the registered source in that resolved plan;
- `model_files`: complete relative path, SHA-256 and byte-size inventory.

Mount a bundle at `/input`, read-only:

```text
run.json
manifest.json       exact stored dataset manifest bytes
examples.jsonl      exact stored example bytes
model/              materialized registered model and tokenizer files
```

The launcher is responsible for obtaining the exact registered Hugging Face commit
or safely materializing the inspected uploaded archive and binding `model_files`
to that provenance. A hash inventory supplied alongside arbitrary files does not
prove their upstream origin. Never expose arbitrary bundle construction as a way
to bypass registry or run authorization. No signed URL or credential belongs here.
The runner checks actual hashes, matching references and parameter/recipe
consistency; it does not contact PostgreSQL or an object store.

Only native Qwen2/Llama/Mistral safetensors models are admitted. The complete model
inventory must match; additional files, symlinks, pickle checkpoints, custom-code
mappings and pre-quantized configurations fail. Individual model files are capped
at 64 GiB, with 4,096 inventory entries. Run/manifest/config inputs are capped at
8 MiB, example JSONL at 64 MiB, 100,000 examples and prepared token JSONL at 512 MiB.
Launchers must also bound total storage, memory and preflight time for their target.

## Translation and loss masks

`app/training/soup.py` owns translation; it does not import Soup. The generated
`soup.yaml` uses JSON syntax accepted by Soup's YAML parser. It explicitly maps
learning rate to `training.lr`, LoRA rank to `training.lora.r`, `all_linear` to
`all-linear`, AdamW to `adamw_torch`, and retains epochs, batching, accumulation,
seeds, scheduler, warmup and weight decay. Engine details fix gradient clipping at
1.0, gradient checkpointing on, packing/FlashAttention off, logging each step and
checkpoint interval 1,000,000. Final output is `/output/adapter`.

Preparation uses the pinned model tokenizer offline. Conversation examples mask
all previous turns and the final assistant prefix; the final response and its
terminating template tokens carry loss. If tokenizing the prompt is not an exact
prefix of the full conversation, preparation fails rather than guessing a boundary.
Documents train their text tokens. No Q&A or cross-document packing is introduced.
Overlong examples fail before Soup. The result is `train.jsonl` plus Arrow shards
at `tokenized/`, consumed using Soup's `pre_tokenized` mode. The runner's hash checks
replace Soup's optional preprocessing-cache metadata; its missing-metadata advisory
is expected. Numeric validity of weights is ultimately checked when Soup loads them.

Soup 0.74 chooses precision according to hardware. Current recipe v1 requires
BF16, so training rejects devices without native CUDA BF16 support before invoking
Soup. It also checks the requested sequence length against model context capacity.
These checks do not guarantee enough VRAM for the selected workload.

The adapter was checked against the pinned release's
[configuration fields](https://github.com/MakazhanAlpamys/Soup/blob/v0.74.0/src/soup_cli/config/schema.py),
[CLI](https://github.com/MakazhanAlpamys/Soup/blob/v0.74.0/src/soup_cli/commands/train.py),
[pre-tokenized training path](https://github.com/MakazhanAlpamys/Soup/blob/v0.74.0/src/soup_cli/trainer/sft.py)
and [TRL label handling](https://github.com/huggingface/trl/blob/v0.29.0/trl/trainer/sft_trainer.py).
No Soup Python modules are imported by KitchenSoup or its test harness.

## Launch contract

A launcher supplies the actual Docker image ID or registry digest using
`--image-digest sha256:...`, and runs that same content-addressed image. The runner
records this value; it cannot attest its own container digest. Preserve images
needed for reproduction after Docker pruning.

Use a non-root UID able to write an empty private `/output` mount. Run with no
network, read-only root/input, no capabilities, no new privileges, a bounded `/tmp`
tmpfs, explicit memory/PID/disk limits and GPU access only for training. Pass no
provider/storage credentials or Docker socket. `scripts/test_soup_trainer.py`
provides the concrete Docker invocation for a synthetic bundle. The normal
application Compose stack intentionally does not launch this runner.

The entrypoint accepts `--timeout` (1–604800 seconds, default 86400) for the Soup
process and `--validate-only` for preparation followed by `soup train --dry-run`.
It captures stdout/stderr concurrently, retaining at most 8 MiB each and draining
excess data. Logs can contain source/model diagnostics and are private artifacts,
not ordinary web/container logs. Child environment excludes arbitrary host values
and uses private runtime/cache directories with offline Hub settings. SIGKILL,
container resource exhaustion or storage failure can prevent a final manifest.

## Output and reproduction

The [output schema](contracts/training-output-v1.schema.json) records status
(`succeeded`, `validated`, `failed`), stage, runner/CLI exit codes, private error
summary, run/plan/input identity, required Soup/translator versions, image identity,
command, log truncation and hashed files. Retained `run.json` holds both the human
request and complete resolved input. `soup.yaml` and `train.jsonl` retain the exact
configuration and loss masks. The manifest excludes its own hash.

Only CLI exit zero plus required `adapter_config.json` and
`adapter_model.safetensors` qualifies as training success. Supported top-level
adapter/tokenizer files are listed separately for later registration. Checkpoints
and runtime caches are not advertised as final model outputs. Validation-only
success uses `validated` and lists no adapters. Nonzero CLI exits and timeout (124)
remain failures. A malformed bundle/preflight failure produces a failure manifest
when the output workspace is usable; no partial adapter is advertised.

Output directories must be empty. A retry against a prior workspace fails without
rewriting its manifest. Do not infer success from a directory or adapter file
alone. Registration should verify manifest/file hashes and the expected run/image
identity. Fixed seeds/configuration support reproduction; GPU adapter bytes and
runtime timestamps are not guaranteed to be identical across devices.

## Verification and updates

```sh
make verify
make test-soup       # real CLI dry run + separate CPU training probe; no network/GPU
make test-soup-gpu   # optional actual training; skips if native BF16 is unavailable
```

The CPU training probe directly invokes Soup with the generated config in a separate
synthetic workspace. Soup uses FP32 on CPU; this tests its actual training API and
adapter output, not the BF16 recipe execution contract. The runner never relaxes
its BF16 preflight, and CPU validation manifests remain `validated`.

The smoke fixture contains only synthetic conversations and a tiny random Qwen2
model generated locally inside the image. It checks token masks, CLI status,
manifest identity and refusal to overwrite prior output. It downloads no weights.
The optional GPU test is not a quality benchmark or a production-size VRAM check.
It fails explicitly if Docker cannot expose a CUDA device, and reports a skip when
the CUDA check runs but native BF16 is unavailable. The development GTX 1660 Ti
does not satisfy the current recipe precision requirement.

To update dependencies, edit `images/soup-trainer/requirements.in`, run
`.venv/bin/python scripts/lock_soup_trainer.py`, review the generated lock, rebuild
and rerun CLI/GPU tests. Changing Soup's fields/behavior requires a new translator
version and compatibility review, preserving accepted recipe meaning. Regenerate
wire schemas with `make openapi`. See [ADR 0010](adr/0010-offline-soup-training-runner.md).
