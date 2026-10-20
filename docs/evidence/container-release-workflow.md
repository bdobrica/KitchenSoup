# Tag-triggered container publication checks

Date: 2026-10-20. The release workflow publishes only stable `vX.Y.Z` tags.
It reuses the existing CI gate before accessing Quay secrets, builds the application
and isolated CPU Soup-ingestion images, and generates a digest-pinned Compose
installation with the ingestion image's matching configuration ID. The workflow
smoke-tests that stack and removes its disposable volumes before attaching the
installation files to a GitHub Release. GPU image publication is outside scope.

Checks performed locally:

- `actionlint` 1.7.12 accepted both the reusable CI and publication workflows,
  including Actions expressions and job permissions. The tool was installed
  temporarily outside the repository; no project dependency was added.
- All 12 focused release helper tests passed: stable tag/package/API agreement,
  rejection of malformed image identities, exact digest/configuration binding,
  fail-closed template validation, and unchanged networks/secrets/startup/limits.
- `make verify` passed formatting/lint, mypy and all 482 unit tests.
- The version CLI accepted the existing matching `v0.1.0` metadata. Rendering
  Compose using the already published 0.1.0 identities reproduced the previously
  validated installation byte-for-byte. Docker Compose accepted the generated
  file with validation-only environment values and without starting services.
- Read-only Quay inspection confirmed its missing-tag error format used by the
  preflight guard. Existing images were not changed. Inspection errors other
  than a missing manifest stop publication; existing tags stop publication too.
- GitHub's secret-name listing confirmed `QUAY_USERNAME` and `QUAY_PASSWORD` are
  present. No secret values were fetched, logged or committed. The operator
  configured these credentials; their actual login/write permissions have not
  been exercised by a hosted release run.
- Final diff reviewed for unrelated changes, contract drift and secret patterns.

No new version tag, GitHub Release or image publication was triggered for this
change. The first hosted tag-triggered run, its registry authentication, image
pushes, installation smoke test and release-asset upload remain unverified live.
The runtime baseline's actual image/startup/ingestion checks are recorded in
[container distribution evidence](container-distribution.md); those checks were
not repeated here. No new integration, GPU or paid-provider runs were needed.
Publication is not atomic across both images; partial publication requires a new
patch version. The preflight check cannot exclude races with unrelated publishers.
