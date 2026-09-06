# LLM provider settings

Run `make up` and open **LLM provider settings** at `/settings/providers`.
Choose a name, API base URL, secret reference and one or more model names (one per
line). Save, then choose a configured model and **Test connection** or **Test
structured output**. Edit changes preserve the provider UUID. Saving and listing
settings do not call a provider or read a credential.

Tests send a fixed synthetic prompt and may incur a small provider charge. They
never send dataset content or return provider-generated text. Current imports,
Soup extraction and dataset versions remain local. Future assisted workflows will
send their prompts and selected source content to the configured destination;
an internal gateway can forward requests to another service. Settings display
this distinction explicitly. A future source-bearing action must repeat the
relevant destination/data disclosure before sending content.

## Credentials and configuration

OpenAI defaults are `https://api.openai.com/v1` and `env://OPENAI_API_KEY`. Enter a
model name available to your account; no model is selected or discovered implicitly.
For an internal gateway, set its base URL, for example
`http://gateway:4000`, `http://gateway:4000/v1`, or
`https://llm.example.internal/prefix/v1`, and use a configured gateway alias as
the model name. The client preserves the prefix and appends `/chat/completions`.
Use HTTP only on a network you trust; HTTPS uses normal certificate verification.
URLs cannot embed credentials, query strings or fragments.

| Setting | Behavior |
| --- | --- |
| `api_key_ref: env://OPENAI_API_KEY` | Resolve an operator-enabled server environment name on every call |
| `api_key_ref: file:///run/secrets/provider-key` | Read a regular UTF-8 file under the operator secret directory on every call |
| `api_key_ref: null` | Omit Authorization for a service explicitly configured without authentication |
| `KITCHENSOUP_PROVIDER_ENV_NAMES` | Comma-separated allowlist; default `OPENAI_API_KEY,LITELLM_API_KEY` |
| `KITCHENSOUP_PROVIDER_SECRET_DIRECTORY` | File-resolution root; default `/run/secrets` |

Compose passes `OPENAI_API_KEY` and `LITELLM_API_KEY` from the host environment or
ignored `.env` to web only. Set the real value there through your operator secret
workflow; never enter it in the settings form or commit it. Adding another allowed
environment name also requires explicitly passing that variable into web in your
Compose override. Changing the host environment requires recreating web; a running
process cannot see later changes to its parent's environment.

For files, create an operator-managed `local-secrets` directory and copy
[compose.provider-secrets.example.yaml](../compose.provider-secrets.example.yaml)
to ignored `docker-compose.override.yml`, then run `make up`. The example mounts
that directory read-only at `/run/secrets` for web. Put a credential file there
and make it readable by web's UID 10001, without exposing it to other users.
Both the directory and override are ignored by Git. Never use source-upload
storage or a model/harness workspace as the secret directory.

References reject traversal and encoded/ambiguous file paths. Resolved file paths,
including symlink targets, must remain within the operator-owned directory.
Nonregular files and files over 8 KiB fail. File trailing CR/LF is stripped; bearer
values must otherwise be nonempty printable ASCII without whitespace. Missing,
disallowed or unreadable credentials fail with a sanitized error. Credential
availability is checked only when making a request, not during settings reads.

These are trusted operator settings in KitchenSoup's existing local, single-user
deployment. Internal/loopback destinations are intentionally supported. Do not
expose the settings API to untrusted users; this milestone adds no authentication,
network policy engine or credential broker.

## Provider port and structured output

`app/providers/openai_compatible.py` exposes the `LLMProvider` protocol and HTTPX
adapter. `ProviderService.client(provider_id)` snapshots configuration and closes
the database transaction before returning a client. Call `chat(ChatRequest(...))`
for text or `structured_output(request, ResponseModel)` for a typed JSON result.
These are application service methods, not public arbitrary-prompt endpoints.

The adapter sends nonstreaming Chat Completions with explicit configured model,
messages, `max_completion_tokens` and `store: false`. It makes no model-discovery
requests, follows no redirects, uses no implicit proxy credentials, and performs
no automatic retry. `store: false` does not replace the provider's own retention
policy. Chat Completions is the shared compatibility boundary for this milestone.
See the [OpenAI API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
and [LiteLLM client examples](https://docs.litellm.ai/docs/proxy/user_keys).

Structured output generates `response_format` with `type: json_schema`, a fixed
schema name and `strict: true`. Pydantic object schemas are tightened to fixed
properties with every field required. Defaults are removed from the wire schema;
nullable fields must be returned explicitly. Arbitrary-key dictionaries and
nonobject roots are unsupported. Responses undergo strict Pydantic validation
and a JSON-value round-trip comparison, rejecting extras, missing defaults and
normalization. Use JSON-shaped models with matching validation/serialization
aliases; models relying on coercion or custom transformations are unsuitable.
Provider-specific unsupported schema features fail visibly. See
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

A refusal, truncation, tool call, missing text or invalid structured response is
an error. There is no silent JSON-mode fallback. A successful chat test does not
prove structured-output support; test the latter separately for the chosen model.
Maximum completion tokens default to 4096 (1–16384); tests use 1024. Some models
may need a different budget or may not support these options. No model-specific
sampling defaults, tools, multimodal messages or live discovery are implemented.

## HTTP contract and validation

The additive operations are in the existing [v1 OpenAPI contract](contracts/artifacts-v1.openapi.json):

| Method and path under `/api/v1/llm-providers` | Result |
| --- | --- |
| GET root | 200: configured providers with references, never resolved keys |
| POST root | 201: create from name, base_url, api_key_ref and model_names |
| PUT `/{provider_id}` | 200: replace configuration, preserving UUID and creation time |
| POST `/{provider_id}/test` | 200: sanitized result for `{model, mode: "chat" or "structured"}` |

Names need not be unique; each POST creates a separate provider. Extra properties,
invalid references/URLs/models and unconfigured test models return 422. Missing
providers return 404. Rejected authentication/options, refusals, incomplete or
invalid output return 502. Missing credentials, connectivity/timeouts and rate
limits return 503. All provider responses use no-store, and provider validation
errors omit submitted values. Error responses never include upstream body or
headers. No provider response, prompt, test history or resolved credential is
persisted by these operations.

Messages are limited to 100, each up to 256 KiB characters; the encoded request is
capped at 1 MiB and provider response at 4 MiB. HTTP connect timeout is 10 seconds;
other network-operation timeouts are 60 seconds. These are inactivity limits,
not a total request wall-time guarantee. Future queue/cancellation work remains
separate. The existing `llm_providers` table is reused without a migration.

`make verify` covers mocked OpenAI/LiteLLM-style paths, credential resolution and
rotation, error redaction, bounded responses and structured-output failures.
`make test-integration` verifies persistence and API behavior against PostgreSQL
with a mocked provider. These tests require no real keys or provider calls.
See [ADR 0008](adr/0008-llm-provider-boundary.md) and
[Milestone 9 evidence](evidence/milestone-9.md) for scope and validation.
