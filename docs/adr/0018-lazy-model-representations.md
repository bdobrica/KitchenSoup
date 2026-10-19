# ADR 0018: Lazy local merge and AWQ representations

Status: Accepted

Date: 2026-10-19

## Context

PLAN section 28 and Milestone 17 require LoRA, merged BF16/FP16 and AWQ INT4
representations of one logical model version. ADR 0015 defers merge execution;
ADRs 0011, 0014 and 0017 establish explicit host Docker authority and recovery.

## Decision

Add an artifact-transformation job separate from immutable training runs. Store
an immutable versioned snapshot with selected artifact UUID/hash, exact base source,
precision, calibration and content-addressed runtime image. Hash canonical snapshot
JSON and enforce uniqueness in PostgreSQL: concurrent identical requests reuse
one job and its committed output. Failed/lost attempts are retained and returned;
there is no automatic retry. Different parameters or image identity create a new job.

Merge only a registered successful causal LoRA adapter with its reviewed base;
never follow adapter-supplied base paths. AWQ consumes a registered merged artifact.
Its fixed INT4 GEMM configuration uses group size 128 and zero points. Require
explicit bounded calibration text, stored in the private immutable job snapshot;
do not select training data or download calibration datasets implicitly. Calibration
is omitted from progress responses and never sent to a provider. It affects the
transformation identity and numerical output. Structural verification does not
establish quality; users review outputs separately.

Run Transformers/PEFT merge and AutoAWQ only in a separate pinned image. These are
concrete image-local ML dependencies, absent from the application environment;
KitchenSoup does not import Soup internals. AutoAWQ is archived upstream, so pin
its implementation and validate compatibility rather than track latest releases.
FP16 is the default for Turing; native BF16 is explicit. Preserve the trusted-host
boundary: default Compose gets no Docker access. Launch with no network or secrets,
non-root UID, read-only inputs/root, dropped capabilities, no-new-privileges and
bounded CPU/RAM/PIDs/tmpfs/logs. Native JIT executable mappings are confined to tmpfs.
The operator supplies aggregate workspace disk quotas and avoids GPU overlap.

Commit QUEUED before external work. Worker scans prepare/start jobs; either background
role may recover/observe them with the existing per-UUID PostgreSQL advisory claim.
States are QUEUED, PREPARING, RUNNING, POSTPROCESSING and terminal SUCCEEDED, FAILED,
LOST. Database guards preserve inputs, output membership and state transitions.
Never replay PREPARING after process death: adopt verified running/completed
containers or clean up and terminalize. Missing submitted containers become LOST;
Docker uncertainty leaves state pending. Observe a 24-hour running deadline; cleanup
can be delayed while a preparation call holds its claim. No scheduler or independent
wall-clock kill service is introduced.

Verify successful image/ownership identity, bounded output inventory, input binding,
regular files, hashes and native safetensors archive structure before registration.
Register an artifact on the same model version and append artifact derivation edges
for adapter/base or merged input. Hub-only bases remain exact source provenance,
without fictitious weight artifacts. Do not add logical models or parent edges.
Use deterministic output UUID/object key for registration retries; commit the output
reference while POSTPROCESSING, clean up verified resources, then mark SUCCEEDED.
A cleanup retry reuses committed bytes. An ambiguous object write can leave an
unregistered deterministic object; retention is separate.

Complete merged artifacts can satisfy further training's lazy base requirement;
choose the oldest registered merged artifact deterministically and snapshot its
exact hash while retaining source-license provenance. AWQ is a serving representation,
not a training base. Allow only internally registered AWQ output for serving through
an additive deployment snapshot field; ordinary pre-quantized uploads remain rejected.
Existing adapter serving and previously saved plans remain compatible.

## Consequences

The model page exposes representation formats/sizes, creation and polled job states.
The existing artifact graph exposes derivations. Jobs do not add a queue, workflow
engine, transformation event table, remote executor or cancellation contract.
GPU model compatibility, available memory and calibration suitability remain runtime
constraints. BF16 GPU validation is optional; FP16 merge/AWQ is the local validation
target. A new merged output is lazy and never created by merely browsing a model.
