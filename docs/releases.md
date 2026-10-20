# Release process

The first MVP is `v0.1.0`; package metadata and FastAPI version are `0.1.0`.
A local annotated Git tag identifies the reviewed commit. Creating a local tag
has no external effects. Pushing a new `vX.Y.Z` tag triggers the image publication
workflow described below, including a GitHub Release with installation files.
It does not deploy infrastructure or publish Python packages.

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
are ignored. Releases have no signed provenance, full dependency SBOM or
reproducible-build guarantee.

## Automated container publication

[Publish release images](../.github/workflows/publish-images.yml) runs on pushed
stable tags of the form `vX.Y.Z` (no prerelease/build suffix or leading zeros).
It requires agreement between the tag, package metadata and FastAPI version.
The existing CI workflow is reused to run formatting/lint/type checks, unit tests,
Compose validation, integration tests and the real CPU Soup-ingestion test before
registry credentials become available to the publishing job.

Configure these **repository Actions secrets** in GitHub Settings → Secrets and
variables → Actions:

- `QUAY_USERNAME`: a Quay robot account username, including its namespace prefix.
- `QUAY_PASSWORD`: that robot account's token, with write access to only
  `bdobrica/kitchensoup`. Keep the repository public for anonymous installation.

Local `docker login` credentials are not available to hosted Actions runners.
Do not copy personal credentials into workflows or image build arguments. Only
the publishing job receives the Quay secrets, through Docker's login action.
The release job alone receives `contents: write` for GitHub Release assets;
validation/build jobs use `contents: read`.

After updating versions and completing the release review, commit and push the
branch, then create and push a **new** annotated tag, for example:

```sh
git tag -a v0.1.1 -m 'KitchenSoup 0.1.1'
git push origin v0.1.1
```

The example requires package/API version `0.1.1` first; do not recreate `v0.1.0`.
The workflow builds linux/amd64 images with the release version and source commit
labels, publishing `quay.io/bdobrica/kitchensoup:X.Y.Z` and
`quay.io/bdobrica/kitchensoup:soup-ingest-X.Y.Z`. GPU trainer/vLLM images are outside
this workflow. No moving `latest` tag is published.

Before building, the workflow refuses existing versioned image tags and fails on
unexpected registry inspection errors. Publication of two images is not atomic.
If a job fails after pushing either image, inspect the failure and publish a new
patch version; do not overwrite a successful tag or delete published images to
force a rerun. Builds for a single Git tag are serialized without cancellation.
Quay administrators should separately restrict manual tag writes; a preflight
existence check cannot prevent races with unrelated external publishers.

The workflow generates a release-specific `compose.yaml` from the checked-in
baseline, replacing **both** image pins and the matching ingestion configuration
ID. It validates Compose and starts the published stack with ephemeral credentials
and disposable volumes, then cleans up. The release job creates the GitHub Release
(or attaches files to an existing release) and uploads `compose.yaml` and
`images.md`. Existing assets are never silently replaced. To retry just a failed
GitHub asset upload, rerun that job rather than the image publishing job.

Download a particular release's installation file without a checkout:

```sh
curl -fsSLo compose.yaml https://github.com/bdobrica/KitchenSoup/releases/download/v0.1.1/compose.yaml
```

Use an actually published tag. Generate credentials and start as described in
[installation](installation.md). The checked-in Compose baseline remains the
manually validated 0.1.0 installation; the workflow does not commit generated pins
back to Git. Build cache is separated by app/ingestion. No cloud GPU resources are
allocated. [Docker's Actions documentation](https://docs.docker.com/build/ci/github-actions/)
and the [GitHub release CLI](https://cli.github.com/manual/gh_release_create)
describe the underlying publication tools.
