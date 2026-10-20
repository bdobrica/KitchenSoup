# Model and data licenses

KitchenSoup's Apache-2.0 license covers its own code, not imported weights,
training data, adapters, generated outputs or third-party runtime components.
Check the original model/data license and any use, redistribution, attribution
or acceptable-use conditions before downloading, training, serving or publishing.
KitchenSoup records provenance; it does not decide legal compatibility.

The catalog records license identifiers and URLs. Registration snapshots source,
exact revision, declared license, URL, gated status and notes. Public Hub metadata
without a license is shown as `unknown`; this is not permission. Uploaded archive
licenses are operator declarations, not an inference from safetensors inspection.
Gated/private Hub sources and custom remote code are outside the supported resolver.

Fine-tuned descendants retain base source-license provenance. Model ancestry and
artifact derivations preserve the association through adapters, merged weights
and quantized representations. Transforming bytes does not erase source terms or
prove that all input licenses are compatible. Dataset/source metadata and immutable
manifests retain their declared provenance; missing declarations remain the
operator's responsibility. See [model lineage](models.md#trained-models-and-lineage)
and [representations](representations.md).

The UI displays declarations and warnings; it does not require click-through
license approval or enforce a license allowlist. Structural model compatibility
and successful GPU validation are independent of licensing. Preserve required
upstream license/notice files when redistributing models or runtime images.
See [third-party notices](../THIRD_PARTY_NOTICES.md).
