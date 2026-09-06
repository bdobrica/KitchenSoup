# Milestone 9 validation

Validated on 2026-09-06 with Python 3.13, PostgreSQL 18.6 and RustFS 1.0.0-beta.12.

- `make fmt`, `make openapi` and `make verify` passed formatting, lint, strict types
  and 108 unit tests. Provider tests cover OpenAI defaults, root /v1 and prefixed
  gateway URLs, explicit models, bearer/no-auth modes, env/file references,
  rotation, bounded reads, disallowed paths/names, error redaction, refusals,
  redirects, timeouts, excess output and strict structured-response validation.
- `make test-integration` passed 38 tests with disposable PostgreSQL and RustFS.
  Provider checks use HTTPX mock transport and synthetic values: create/update,
  stable identity, chat/structured tests, model restrictions, absent credentials
  and invalid input redaction. Database rows contain only references; no resolved
  value is stored. Existing ingestion/version/storage integration checks passed.
- No migration or dependency was added. `make migration-check` found no drift.
  `make compose-check` and quiet validation of the optional secret-file override
  passed. The existing published API paths and component schemas remain unchanged;
  provider operations are additive.
- `make up` started seven long-running services healthy. Headless Chromium
  registered an OpenAI configuration using its default URL/reference without
  calling OpenAI. A temporary local fixture HTTP server accepted a gateway base
  with a path prefix and verified a synthetic, unauthenticated request. Chat and
  structured test buttons succeeded. Editing preserved identity, settings survived
  reload, and a disallowed credential reference produced a sanitized error.
- The browser showed the destination and data-sharing notice. Test requests had
  only fixed synthetic prompts; no source material, real credential, OpenAI
  request, LiteLLM account request or billable provider call was used. Browser
  tooling and the fixture server remained outside the repository in /tmp.
- Synthetic provider rows and the temporary fixture container were removed.
  `make down` stopped the stack, retaining existing local volumes and ignored
  credentials. Real environment values and signed storage URLs were not printed.

Compatibility was checked against official OpenAI structured-output/API documentation
and LiteLLM client examples, with mocked transports and a local wire-compatible
fixture. This is not a claim of live compatibility with every model, schema or
LiteLLM deployment. File-secret behavior was unit-tested; a live mounted credential
was not used. Existing Starlette/AnyIO deprecations and the intentional duplicate-ZIP
fixture warning remain unrelated. Training, assisted source-data workflows and
paid-provider availability checks remain outside this milestone.
