# OIDCGate perimeter example

KitchenSoup delegates authentication to a reverse proxy; all admitted users share
one operator's data and execution authority. This example is a host deployment:
TLS ingress → loopback OIDCGate `:4180` → loopback KitchenSoup `:8000`.
It uses the upstream [OIDCGate configuration](https://github.com/bdobrica/OIDCGate/blob/1b30b5bfc5b08c87adff7b4ed23c5f394c389237/oidcgate.example.yaml)
and [operations reference](https://github.com/bdobrica/OIDCGate/blob/1b30b5bfc5b08c87adff7b4ed23c5f394c389237/docs/operations.md).
OIDCGate is a separate project, not a KitchenSoup dependency or authentication API.

Copy [the configuration](../deploy/oidcgate/oidcgate.example.yaml) to a private
operator directory. Configure your issuer, resource audience, browser/device client
IDs, scope and restricted operator group. Register the exact redirect URI
`https://kitchensoup.example.internal/_oidcgate/callback` in your provider. Supply
the browser client secret and a random base64-encoded 32-byte session key through
OIDCGate's environment, never committed YAML or a model workspace. OIDCGate expands
`${NAME}`; do not expand secrets into logs. Keep authentication bypass disabled.

Build/install the separate OIDCGate binary at the referenced revision using its
own documented toolchain. Validate and start it with the same secret environment:

```sh
oidcgate validate-config --config /private/operator/oidcgate.yaml
oidcgate serve --config /private/operator/oidcgate.yaml
```

Use a TLS reverse proxy on the public host that forwards the entire origin,
including `/_oidcgate/`, to `http://127.0.0.1:4180`. Restrict direct access to
ports 4180/8000 and every RustFS/backend port. For containers, loopback is per
network namespace: adapt upstream/listener addresses and deny bypass using a
private network; do not publish KitchenSoup directly. Configure header/body idle
timeouts and streaming flush behavior for chat/SSE; do not log request bodies,
cookies, authorization headers or signed object URLs. OIDCGate's example imposes
no request-body cap so KitchenSoup's own upload limits apply.

Before network access, test HTML login, JSON API 401 without a bearer/session,
403 for disallowed groups, browser callback/logout, authenticated uploads, chat
and SSE, and inability to contact the backend around the proxy. Protect unsafe
browser requests against cross-origin attacks at the perimeter; KitchenSoup has
no general CSRF layer. TLS ingress/session integration is operator configuration,
not covered by service-free tests. No live identity-provider login is claimed.

The default ArtifactStore endpoint/CORS is loopback. A remote browser needs a
separately secured reachable S3 endpoint and exact allowed origin. Do not simply
publish the RustFS console/API or assume a browser cookie authenticates S3 SDK
requests. Keep the application local until that storage path is deliberately
configured and tested. See [storage](storage.md) and [security](security.md).
