# ADR 0009: Engine-independent intent and immutable review plans

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 15 and 18 require understandable intent, versioned recipes and an
AppSpec separate from engine configuration. ADR 0001 reserves TrainingRun for
submitted inputs, including an execution target. Milestone 10 precedes the Soup
translator, executors and submission state machine.

## Decision

Publish `kitchensoup.appspec/v1`, `kitchensoup.recipe/v1` and
`kitchensoup.resolved-plan/v1` from strict Pydantic definitions. AppSpec contains
intent, registered model-version and dataset-version UUIDs, an optional pinned
recipe reference, and bounded numeric overrides. Guided intents have explicit
version-pinned recipe defaults. Advanced requires an explicit recipe. Recipes
are packaged JSON data, with no executable content, provider configuration,
credential references or engine configuration. Recipe identity plus version has
immutable meaning; changed defaults require a new recipe version.

Initial conversation imitation and task recipes consume conversation-only dataset
versions and train the final assistant target in each example. The initial document
recipe consumes document-only versions and adapts to plain text without synthetic
Q&A. Mixed selections fail explicitly. Chat-template application and tokenization
belong to the future translator; overlong examples must be rejected, not silently
truncated. These initial recipes are configuration candidates, not claims of GPU
capacity, numerical quality or validated Soup compatibility.

Resolve against registered source provenance, never an updated catalog projection
or a mutable dataset draft. Require exactly one ungated model source: a full
Hugging Face commit (including catalog registrations), or a registered uploaded
artifact. Retain the source snapshot, archive hash when applicable, immutable
dataset manifest/example hashes, complete recipe and recipe hash, resolved numeric
parameters, output intent and review warnings. The dataset manifest remains the
metadata authority; plan resolution does not reread large artifacts or download
model weights. Training preparation must verify the actual referenced bytes and
model/hardware/engine compatibility before execution.

Persist review results in a separate TrainingPlan table with restrictive model and
dataset version foreign keys. A generated relational migration and a separate SQL
trigger make all plan columns immutable. Saving creates a new snapshot; it never
creates a queued TrainingRun, grants execution authority or selects an executor.
The browser previews first and supplies the preview hash when saving. A changed
resolution fails with 409 and requires a new preview. Saved plans are read from
their retained payloads, not resolved again through current recipes or catalog data.

## Consequences

The guided UI hides ML fields by default, provides exact dataset-preview links,
and offers advanced parameter overrides and JSON inspection. Document guidance
explains that retrieval may suit changing factual knowledge better than fine-tuning.
No provider call, Soup invocation, GPU job or queue operation occurs in this slice.

Payload schema versioning is independent of Alembic database versioning. Unknown
versions and extra fields fail closed. Existing plan bytes and recipe meanings
are never silently migrated; a future reader/migrator must explicitly support each
old version and create a new snapshot if the requested semantics change. Future
submission adds validated executor and pinned engine details to its own immutable
run snapshot, preserving the original user intent and reviewed parameters.
