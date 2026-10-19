# ADR 0016: Frozen prompt suites and local paired evaluation

Status: Accepted

Date: 2026-10-19

## Context

PLAN section 29 and Milestone 15 require a small human comparison of base and
fine-tuned responses, before merged representations and vLLM serving exist.
ADRs 0009–0015 already preserve reviewed inputs, local GPU authority, immutable
training attempts and registered adapters. Evaluation must not change training
semantics or confuse an adapter with complete base weights.

## Decision

Create named suites containing 3–10 ordered plain user prompts. Saving through
the API creates the suite and prompts together; no edit/delete API is provided.
Select an optional suite UUID when submitting a saved training plan. Keep that
association in the existing immutable TrainingRun foreign key, independently of
the AppSpec/plan hash. Cloning retains the same suite. Lock the suite during
submission and reject additional prompt inserts once any run references it;
PostgreSQL rejects suite/prompt updates and deletes. Existing pre-feature rows
are not rewritten or retroactively associated.

After successful output registration, an explicit evaluation request commits a
QUEUED EvaluationTask. The host worker scans PostgreSQL independently of Valkey;
requests and reads do not run inference. Evaluation tasks extend the worker's
command handling with a bounded synchronous paired-generation operation rather
than a deployment or serving endpoint. Training lifecycle commands retain ADR
0014's semantics. The same run advisory claim serializes evaluation generation,
repeat requests, preferences, reports and terminal training cleanup. No GPU
scheduler or new daemon is introduced; operators avoid simultaneous GPU work.

Use the immutable training image identity as an offline ML runtime, with a
separate application-owned inference module, not Soup internals or training CLI
changes. Build the new image before submitting runs with evaluation suites.
Materialize and verify the exact reviewed base input and registered adapter once
per evaluation attempt. Each answer runs in a fresh sequential local GPU container
with no network, credentials or Docker socket, read-only inputs/root, the non-root
operator UID, dropped capabilities and bounded CPU/RAM/PIDs/output/time. The base
container sees no adapter bytes; the tuned container receives only the verified
adapter representation. No merged weights are created. Load native Transformers
safetensors locally with remote code disabled and PEFT causal LoRA adapters only.
These libraries remain in the existing locked trainer image; the web application
gains no ML dependency. Inference is not a vLLM deployment and confers no provider
or deployment authority.

Publish inference input/output v1 contracts. Reverify the complete training bundle,
adapter inventory and configuration inside the image. Both models use the same
user chat template and greedy generation settings (128 new tokens maximum, one
beam), and the reviewed FP16/BF16 precision. Reject overlong prompts instead of
truncating; FP16 is supported on compute capability 7.5 without requiring BF16.
Bound each answer container to ten minutes and verify its input hash before
accepting its response. Do not log prompts, generated content or third-party
exception bodies. Stop/remove only containers with matching task/image identity.

Commit each generated response as a hashed artifact and a unique run/prompt/model
result before continuing. Results are reusable across explicit attempts for that
run; the generation settings are fixed in v1. A worker crash leaves RUNNING;
the next worker stops a matching remaining container and marks that attempt FAILED
without replaying uncertain work. A failed task requires an explicit new request;
completed results survive and missing results can be generated. Queue hints,
model output and report data do not establish execution authority.

Store one mutable paired preference on the fine-tuned result: base_better,
fine_tuned_better or same. PostgreSQL enforces local-run suite/model membership,
response identity immutability and preference values. The report v1 snapshot
contains prompts, both answers, preferences/counts, exact run/suite/model IDs,
plan hash, image and generation settings. Register content-addressed JSON reports
with derivation edges to response artifacts. Each preference invalidates older
current pointers and creates a new report snapshot, preserving old artifact bytes.
Expose side-by-side text rendering, downloads and model-page summaries through
additive v1 APIs and packaged UI, with no remote scripts/renderers.

## Consequences

The comparison answers the user's own test prompts; it is not an automated
benchmark or evidence of improved model quality. Do not train on the evaluation
prompts when testing generalization. Suites must be selected before training;
existing runs cannot acquire a new evaluation suite after submission.

Generation holds a separate database advisory-claim transaction but no run row lock;
reads remain available. A long evaluation occupies one worker; additional workers
may handle commands, but GPU scheduling/capacity arbitration remains future work.
Every response loads models separately, favoring simple isolation over throughput.
Temporary input directories are removed on ordinary completion/failure; a killed
host process can leave private orphan workspaces/objects for operator retention.
No external provider, merge, quantization or vLLM lifecycle is added. GPU smoke
validation establishes FP16 base/adapter loading and generation, not model quality.
