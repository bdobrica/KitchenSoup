# ADR 0003: Curated catalog and inspected model sources

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 8–10 assign model browsing, logical versions, and source-license
provenance to KitchenSoup. Milestone 4 adds registration before executable
training recipes and deployment adapters exist.

## Decision

Ship a versioned JSON catalog in the application package and validate it with
Pydantic. JSON avoids adding a YAML parser for this small data source. Generate
the published JSON Schema from the same definitions. Synchronize catalog rows
explicitly, preserving their UUIDs and retiring removed entries without deleting
referenced records. The packaged catalog owns curation; PostgreSQL is its browsing
projection. The registry remains authoritative for registered versions and sources.

Each registration creates a logical model, version 1, and a source in one explicit
transaction. Choosing the same catalog key/revision reuses its existing model.
Other import requests create separate logical models, even for identical inputs.
Copy the source revision and license into the registered source; catalog updates
must not rewrite that provenance. Uploaded sources reference the verified artifact
UUID and its SHA-256 instead of inventing a Hugging Face revision.

Resolve public Hugging Face metadata anonymously through an explicit resolver
port. Resolve branch/tag input to a full commit and read configuration at that
commit. Accept only public, ungated native causal-language-model structures.
Do not download remote weights, import model code, or use implicit local tokens.
Missing license declarations are shown as `unknown`; archive licenses are user
declarations, not inferred grants.

Inspect uploaded ZIPs through ArtifactStore without filesystem extraction. Bound
compressed input, archive directory size, entry count, expanded size, compression
ratio, JSON/header metadata, and inspection time. Reject unsafe paths, duplicate
names, links, special files, pickle checkpoints and custom model/tokenizer code.
Validate safetensors headers, byte ranges, and shard references without loading
weights. Keep the original uploaded archive unchanged.

## Consequences

The initial structural allowlist is Qwen2, Llama, and Mistral causal models using
safetensors. ZIP stored/deflate archives are supported; other formats, remote code,
adapters alone, pre-quantized imports and checkpoint conversion remain unsupported.
Successful inspection is not proof of numerical validity or engine compatibility.
Catalog compatibility and VRAM guidance explicitly distinguish upstream support,
weight-only estimates and pending KitchenSoup execution validation. Catalog entries
and model metadata do not grant execution authority.

The existing database schema supports this slice without a migration. Registry
services use the synchronous transactions from ADR 0001. Model archive upload
uses the staging/finalization contract from ADR 0002. Failed inspection leaves
the raw artifact available but creates no model records; retention remains separate.
