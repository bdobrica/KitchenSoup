# ADR 0008: LLM providers and operator-owned secret references

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 17 and 27 require replaceable OpenAI-compatible providers, explicit
model names and secret references. ADR 0001 already supplies provider metadata
rows and synchronous database transactions. Dataset conversion currently makes
no external LLM calls; assisted workflows remain later work.

## Decision

Use a synchronous `LLMProvider` protocol with chat and typed structured-output
methods, consistent with ADR 0001's blocking service model. Reuse HTTPX for a
bounded Chat Completions adapter rather than adding an SDK. Keep configured model
names explicit and reject unconfigured names. Do not discover models, import
provider code or let responses choose credentials, destinations, tools or options.

Store only name, base URL, credential reference and model names in the existing
provider table. Create/update does not resolve credentials or contact the provider.
Capture configuration and close the database transaction before network work.
Re-resolve credentials for every call, allowing environment/file rotation without
storing resolved keys in configuration, logs, model state or response DTOs.

The local credential resolver accepts `env://NAME` only for operator-enabled names
(default OPENAI_API_KEY and LITELLM_API_KEY), and absolute `file://` references only
when their resolved paths remain under an operator-owned secret directory
(default /run/secrets). Reject nonregular files, oversized or invalid bearer tokens
and traversal references. Bound reads to 8 KiB; file credentials allow a final
newline. Operators own the environment, secret directory and any symlinks; these
locations must not be writable by source content, model output or untrusted users.
This resolver is a replaceable adapter, not a new cross-component authority.

Base URLs accept HTTP(S) with an optional path prefix, rejecting embedded credentials,
query parameters, fragments and control characters. Append /chat/completions to
the configured base; preserve /v1 and custom prefixes. Permit internal/private
endpoints because self-hosted gateways are an explicit requirement. These are
trusted operator settings under the existing single-user deployment model.
Disable redirects and implicit environment proxies; retain TLS verification.

Requests are nonstreaming, bounded and explicitly disable optional completion
storage. Reject refusals, incomplete output, tools, invalid responses and excess
response bytes. Do not automatically retry billable calls or downgrade structured
requests. Normalize errors without upstream response bodies, headers, prompts,
credential values or provider-generated content. Provider validation errors omit
submitted values so accidentally pasted secrets are not echoed.

Structured output takes an application-defined Pydantic response model, emits a
strict JSON Schema request and validates returned JSON locally. Object properties
are fixed; all fields are required on the wire, including nullable/defaulted fields.
Reject output changed by validation (including ignored extras or inserted defaults).
The helper supports JSON-shaped object models; it does not promise every JSON
Schema keyword or model/provider combination is supported. Provider rejections
remain visible instead of silently weakening the contract.

The settings UI identifies the provider destination, credential reference and
configured models. It explains that future assisted workflows send prompts and
selected content outside KitchenSoup, and that internal gateways may forward it.
Test actions send a fixed synthetic chat or structured prompt only. They can incur
a provider charge, return sanitized status and never accept arbitrary source text.
No current dataset action is wired to the new provider client.

## Consequences

No database migration, runtime dependency or training authority is added. OpenAI
URL/reference defaults are available without guessing a model name. HTTP is useful
for local gateways but has no transport confidentiality; operators should use
HTTPS when the network is not trusted. Model capability and availability remain
provider configuration concerns.

Compose passes the two default provider environment names only to web, not Soup,
worker or reconciler. Optional secret-file mounts are read-only and documented.
Future source-bearing actions must disclose the destination and selected data at
the action, not rely solely on the settings notice. Provider errors and structured
output validation are failure signals, not authorization or safety decisions.
