# Milestone 5 validation

Validated on 2026-09-06 using Python 3.13, PostgreSQL 18.6 and
RustFS 1.0.0-beta.12.

- `make fmt`, `make openapi`, and `make verify` passed formatting, linting,
  strict types and 61 unit tests. Focused source/model tests passed after the
  shared ZIP validator change, including rejection of ZIP64 directory headers
  before ZipFile construction.
- `make test-integration` passed 22 tests using disposable PostgreSQL/RustFS.
  New coverage includes direct upload and unchanged retrieval, document metadata,
  concurrent repeat attachment, dataset isolation, source removal while retaining
  shared/raw artifacts, rejected source admission without partial records, and
  removal protection for canonical documents, conversation imports and versions.
- Archive tests cover safe paths, symlinks, case collisions, file/directory
  conflicts, count/size/ratio limits, CRC corruption, and workspace cleanup after
  success and failure. Original bytes and line endings remain unchanged.
- `make migration-check` found no new upgrade operations; the existing schema
  supports this milestone without migrations. `make compose-check` passed.
- `make up` built the application, completed both initialization services, and
  started all six long-running services healthy.
- Headless Chromium created a dataset, uploaded text and ZIP sources, displayed
  their metadata, and downloaded byte-identical originals. It removed a text
  source and downloaded the retained original through the offered UI action.
  Browser tooling stayed temporary outside the repository.
- An explicit comparison against the previous OpenAPI document confirmed all
  existing operations and schema definitions unchanged; dataset routes are additive.
- Synthetic browser records and objects were removed and `make down` stopped the
  stack, retaining local volumes, catalog data and ignored credentials.

Test warnings are upstream Starlette/AnyIO deprecations and the intentional
ZIP duplicate-name rejection fixture. Maximum-size/concurrency benchmarks,
canonical ChatGPT import, document parsing, GPU execution and permanent raw
object deletion were not performed and are outside this milestone.
