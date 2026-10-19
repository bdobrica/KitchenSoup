# ADR 0013: FP16 GPU validation completes Milestone 12

Status: Accepted

Date: 2026-10-19

## Context

ADR 0012 requires actual GPU smoke evidence for each precision. The available
RTX 2070 SUPER supports the reviewed FP16 execution path but lacks native BF16.
The user explicitly revised Milestone 12 acceptance to require FP16 validation
only; BF16 validation is optional.

## Decision

Supersede ADR 0012's requirement for evidence for each precision as a Milestone 12
completion gate. A successful actual FP16 Soup fine-tune through the local executor
on a compatible NVIDIA GPU is sufficient. The smoke must verify the successful
runner manifest and adapter hashes, alongside the existing lifecycle checks.
CPU validation and a CUDA availability probe alone do not satisfy this gate.

Retain BF16 support, recipe defaults, explicit reviewed precision, hardware
preflight and all execution/security boundaries from ADRs 0009–0012 unchanged.
Record the tested GPU, precision, image identity, checks and limits in the existing
evidence area. Do not claim BF16 GPU execution without a corresponding actual run.

## Consequences

The successful RTX 2070 SUPER FP16 executor smoke closes Milestone 12. BF16 GPU
validation can be performed separately and does not block Milestone 13. This
acceptance validates local execution and artifact integrity, not model quality
or the memory requirements of larger models.
