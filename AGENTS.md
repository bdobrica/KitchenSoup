# AGENTS.md

This repository is one component of the modular, vendor-neutral **ThinkPixel** platform.

Make the **smallest coherent change** that satisfies the task while preserving this repository's ownership boundary, published contracts, compatibility obligations, and the cross-component security model. Do not fix unrelated issues opportunistically; report them separately when relevant.

## Read before changing

Before modifying behavior:

1. Read the relevant accepted records in `docs/adr/`.
2. Read the affected contracts, API/schema definitions, and normative security/architecture documentation.
3. Read `PLAN.md` and `TODO.md` for current implementation intent when they exist.
4. Treat `README.md` as orientation, not as the normative architecture specification.

When sources conflict, use this order for intended behavior:

**accepted ADRs → versioned contracts/API schemas → normative security/architecture docs → PLAN.md → TODO.md → README.md**

Code and tests are implementation evidence, not architectural authority. Do not silently weaken an accepted decision, security invariant, or published contract merely to match current implementation.

If the authoritative sources are ambiguous or incomplete, prefer the change that preserves existing external behavior and component boundaries. Do not invent a new cross-component responsibility or security rule without an explicit architectural basis.

## Engineering rules

* Preserve the component boundary described in `ALIGNMENT.md`; do not absorb another ThinkPixel component's responsibilities for convenience.
* Keep integrations replaceable. Put provider-, harness-, storage-, policy-, and ThinkPixel-specific behavior behind explicit ports/adapters.
* Do not create direct cross-repository database access or depend on another repository's `internal` implementation types.
* Cross-component behavior must use versioned wire/schema contracts and stable identifiers.
* Preserve backward compatibility for published contracts unless an accepted architectural decision explicitly permits a breaking change.
* Do not let Skills, marketplace metadata, Workspace membership, memory, model output, or guardrail results expand Run authority.
* Keep credentials and long-lived secrets outside untrusted agent/harness state. Never commit secrets, credentials, tokens, or sensitive runtime payloads to source, fixtures, logs, documentation, or evidence.
* Treat caches, indexes, projections, and derived state as non-authoritative unless an accepted ADR explicitly says otherwise.
* Public API/schema changes require corresponding contract, compatibility, documentation, and test updates.
* Accepted ADRs are immutable in meaning. Supersede an accepted ADR with a new ADR instead of rewriting architectural history.
* Avoid speculative abstractions and unrelated refactors.
* New dependencies require a concrete repository-local justification.
* Do not hand-edit generated artifacts when a source definition or generator exists. Modify the source and regenerate using the repository's documented workflow.
* Follow existing repository conventions unless they conflict with a higher-authority source listed above.

## Repository and documentation hygiene

* Keep the root `README.md` concise: purpose, status, quick start, key concepts, and links to durable documentation.
* Do not duplicate `PLAN.md` in the README.
* Move durable implemented decisions into `docs/adr/` and durable reference material into `docs/`.
* Prefer Mermaid for architecture diagrams and relative links for repository-local documentation.
* Use RFC 2119/8174 normative terms only when the requirement is intentionally normative.
* Keep current implementation sequencing and remaining work in `PLAN.md`/`TODO.md`.
* Keep release, validation, and implementation evidence in `docs/evidence/` or the repository's existing evidence area.
* Remove completed TODO/plan items when appropriate rather than using `TODO.md` or `PLAN.md` as a changelog.

## Verification

* Use the repository's documented developer commands. Prefer the root aggregate verification target (for example, `make verify`) when one exists.
* Run focused tests first, then the broadest practical repository gate for the change.
* Tests should validate intended contracts and invariants, not merely preserve accidental current behavior.
* Run generators, formatters, linters, schema checks, migrations, and compatibility checks when the affected area requires them.
* Do not claim that a test, migration, generator, live-provider check, deployment check, or other verification step was run when it was not.
* If verification cannot be run, state what was not verified and why.
* If a change alters a ThinkPixel integration boundary or shared convention, update `ALIGNMENT.md` and the relevant contract documentation in the same change. Add or supersede an ADR when the architectural decision itself changes.

## Completing implementation

After a successful implementation:

* Update `TODO.md` and `PLAN.md` when the change affects current sequencing, remaining work, or implementation intent. Do not update them solely to record that a completed task occurred.
* Record durable verification or release evidence in the repository's established evidence area when required.
* Review the final diff for unrelated changes, generated-file drift, accidental secrets, and unintended contract changes.
* Summarize what changed and what verification was performed.

When the task and execution environment permit committing changes:

* Commit the completed coherent change to the active branch to preserve work lineage.
* Use one of these commit prefixes as appropriate: `docs`, `feat`, `build`, `fix`, or `ci`.
* An optional component scope may be added, for example `feat(runtime): ...`.
* Keep the commit message concise and specific to the implemented change.
* Include related `PLAN.md`/`TODO.md` updates in the same logical commit unless repository conventions require otherwise.
