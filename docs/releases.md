# Release process

The first MVP is `v0.1.0`; package metadata and FastAPI version are `0.1.0`.
A local annotated Git tag identifies the reviewed commit. Tagging does not upload
packages, publish container images, deploy infrastructure or push to a remote.
Publishing any of those is a separate explicitly authorized operation.

For each release:

1. Review relevant ADRs/contracts, supported Python/GPU/runtime pins, license and
   notice inventory, unresolved safety limitations and migration compatibility.
2. Update `pyproject.toml`, the application version if changed, and CHANGELOG.
   Regenerate API/schema artifacts with `make openapi`; inspect compatibility.
3. Run `make verify`, `make compose-check` and `make test-integration`.
   Review command/example parsing, local Markdown links, package contents and
   regenerated contract drift. CI also exercises real CPU document ingestion.
4. Use existing GPU/provider evidence only for the exact backend/version tested;
   record new validation separately when engine/runtime behavior changes. Never
   rerun paid validation implicitly. Record skipped checks and limitations.
5. Back up retained data/staging before migration. Stop/clean up active resources
   while reconcilers still run. Review the diff for secrets, generated drift and
   unrelated changes; commit the coherent release change.
6. Ensure `git status --short` is empty, then create an annotated tag, for example
   `git tag -a v0.1.0 -m 'KitchenSoup 0.1.0 MVP'`. Verify with
   `git show --no-patch v0.1.0`. Never move a published tag; fix forward.
7. Only with publication authorization, push the commit/tag and publish reviewed
   artifacts/release notes. Do not include `.env`, journals, credentials, model
   bytes or local runtime data. If signing is configured, use signed release tags.

Package review can use `python -m pip wheel --no-deps --no-build-isolation . -w dist`
inside the prepared virtualenv (requires the build backend). Inspect the wheel's
METADATA, license and packaged templates/static/catalog resources. Build outputs
are ignored. Releases currently have no automated package/image publication,
signed provenance, full dependency SBOM or reproducible-build guarantee.
