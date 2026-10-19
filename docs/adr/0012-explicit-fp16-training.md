# ADR 0012: Explicit FP16 training on older CUDA GPUs

Status: Accepted

Date: 2026-10-19

## Context

The BF16-only execution policy in ADRs 0010 and 0011 excludes compute capability
7.5 devices such as the RTX 2070 SUPER. Soup 0.74.0 supports FP16 training on
CUDA hardware without native BF16. Users need to review this precision explicitly.

## Decision

Supersede only the BF16-only execution restrictions in ADRs 0010 and 0011.
Extend the v1 training parameters and AppSpec overrides with `fp16` alongside
`bf16`. Packaged recipe v1 bytes and BF16 defaults retain their original meaning.
Omitting precision continues to resolve to BF16; no automatic fallback occurs.

The pinned Soup SFT backend selects BF16 on native-BF16 CUDA hardware and FP16
otherwise with `auto_mixed_precision` disabled. Preflight requires CUDA and checks
that this selection matches the reviewed precision. FP16 on native-BF16 hardware
is rejected because the pinned CLI does not expose an explicit SFT precision
switch. Translator v2 adds this preflight policy; generated Soup configuration stays
unchanged for BF16. No Soup imports, patches, new dependencies or CPU execution
path are added.

Precision is retained in the resolved plan and bound by existing plan/input hashes.
For backward compatibility, a null or omitted precision override is omitted from
canonical AppSpec hashing. Old BF16 plan, recipe and runner hashes remain valid;
explicit precision selections produce new snapshots. Older readers reject FP16
payloads; deploy updated control-plane and runner images together.

## Consequences

The UI and API permit explicit FP16 review. GPU probes and synthetic smokes accept
a precision selection. Hardware compatibility does not guarantee workload VRAM fit
or numerical quality. Actual GPU smoke evidence remains required for each precision.
All materialization, sandboxing, loss masks and run authority boundaries remain as
specified in ADRs 0009–0011.
