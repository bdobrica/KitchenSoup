# Milestone 25 validation

Date: 2026-10-19

Runpod training and shared Pod lifecycle infrastructure are implemented through
the existing training protocol. See [setup and limits](../runpod-training.md) and
[ADR 0024](../adr/0024-runpod-training.md). Live BF16 training, verified adapter
registration and provider cleanup passed; local FP16 checks are reported separately.

## Completed checks

- `make verify`: formatting/lint passed, strict mypy passed for 159 source files,
  and **409 unit tests passed**, including 26 Runpod unit tests. Runpod coverage includes bounded configuration,
  catalog price/VRAM/availability checks, pagination, allocation retry restrictions,
  safe errors, public-IP SSH validation and explicit host trust. SSH tests also
  reject transfers after an allocation deadline, including empty files.
- `make test-integration`: **100 passed** using disposable PostgreSQL, RustFS and
  Valkey through the authorized local SSH session. Migration base/head round trips
  and ORM schema-drift checks passed. Fourteen Runpod tests cover immutable target
  APIs/SQL, read-only probes, shared cross-target concurrent reservations,
  uncertain creation, definite rejection, incompatible allocation cleanup,
  started-process adoption after worker death, output registration, transfer and
  manifest failure, cancellation, expiration and terminal cleanup retry.
- After the final collector race fix, `make verify` and the **14 focused Runpod
  integration tests passed** again. The full integration run preceded that fix.
  Provider APIs and remote training are fixtures
  in these database tests; the database and artifact store are real.
- `make openapi` regenerated additive target/run API definitions and
  `runpod-target-v1.schema.json`. Existing AppSpec, reviewed plan, runner input,
  runner output and adapter contracts retain their meaning.
- `make build-runpod DOCKER_BUILD_NETWORK=host` built the thin SSH-capable trainer.
  Host networking was necessary because this machine's Docker build bridge could
  not reach Debian mirrors; the image uses HTTPS package repositories.
- `make test-runpod-image` passed real local authenticated SSH/SFTP, UID/GID 10001,
  immutable input hash validation, fixed `/input` and `/output` bindings, and actual
  Soup CLI CPU dry-run validation. Temporary containers and test keys were removed.
- `.venv/bin/python scripts/test_runpod_image.py --gpu` completed actual FP16 Soup
  training on the local RTX 2070 SUPER using the same direct runner helper. The
  success manifest and adapter output were verified. This validates the image and
  direct runner locally, not Runpod provisioning or model quality.
- The final trainer image was published to the operator-authorized public Quay
  repository. Anonymous manifest retrieval verified its digest:
  `quay.io/bdobrica/kitchensoup@sha256:112204d70ed2fc7e88792ceee2d3179d2901c075c79c0389e76d638720ffc561`.
  The compressed layers total 4,230,429,898 bytes. The development image remains
  cached locally; no private keys or credentials were included in build context.
- Final changed-file scans found no values from configured credential variables or
  embedded private keys. Browser automation/Node.js checks were not run; Node.js
  is unavailable. HTTP/template and generated-contract regression tests passed.

## Live provider acceptance

The operator supplied a Runpod API key through the ignored `.env`, registered an
SSH public key with Runpod and authorized a live smoke budget of at most $3.
Read-only REST v2 catalog, Pod inventory and registered-key access succeeded.
Quay publication was explicitly authorized separately.

The FP16 smoke was attempted using the published pinned image and the normal
durable application service. The V100 catalog preflight rejected the attempt
because capacity disappeared before allocation. It failed **before creating a
training run or provisioning intent and before issuing Pod creation**. The
immutable synthetic execution target remains in local development metadata.

The operator explicitly approved the cheapest available compatible GPU and BF16.
New immutable BF16 plans were created; no FP16 plan was silently changed. Two
Community RTX 3070 Pods at $0.13/hour were created and confirmed terminated. The
first hit the original three-minute readiness limit; the second started sshd but
had no public TCP mapping, so it was canceled through the durable application
lifecycle. No training inputs were sent to these networking-incompatible Pods.
Readiness now allows ten minutes within the allocation deadline and checks durable
cancellation during waiting. A Secure RTX 3090 preflight subsequently lost capacity
before allocation and created no Pod.

A Secure B300 MIG slice at $0.50/hour provided full public-IP SSH. Its actual GPU
probe reported compute capability 10.3 and matching requested/effective BF16.
Soup completed both synthetic training steps and exited zero. That live attempt
revealed a completion race: a second status observation could see completion before
the first observation had triggered artifact collection. The application therefore
failed verification and terminated the Pod without registration. The collector now
returns a running observation directly and collects outputs before terminal
verification; a focused regression test passed.

A follow-up bounded B300 run completed successfully after that fix:

- Run `67184174-4a56-401e-a0ac-f9bad145d2a9` reached `SUCCEEDED` using reviewed BF16.
- The normal ArtifactStore/registry path verified and registered adapter version
  `de514225-b8a5-5e42-86bb-7fe854ee5dc2` with the immutable run's lineage.
- Pod `584eu1jfehe7bd` reached resource state `RELEASED`, cleanup `CONFIRMED`.
- Independent final inventory returned zero Pods; individual inspections confirmed
  all four temporary allocations absent, including the failed and canceled attempts.

The provider billing API reported $0.00 at the final check. Billing can lag; this
is not a claim of free execution. The four allocations' configured GPU price and
15-minute lifetime ceilings total $0.40 in GPU compute; storage and other charges
are additional, within the operator's $3 test allowance rather than a hard provider
billing cap. No AWS resources were created or changed. Local development
infrastructure and synthetic metadata remain available.
