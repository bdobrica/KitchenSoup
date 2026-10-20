# Security assumptions

KitchenSoup's MVP trusts a single operator and has no internal authentication,
per-user authorization or tenant isolation. Every user admitted by an external
proxy can act as that operator, including configuring destinations and consuming
GPU resources. Identity headers do not create row ownership. Bind to loopback
or put the entire application behind an authenticated, access-restricted perimeter.
See [OIDCGate deployment](oidcgate.md).

Keep web, API/docs, artifact signing, settings and playground routes within that
boundary. Block direct backend access and unauthorized cross-origin state changes
at the perimeter. KitchenSoup does not provide a general CSRF protection layer.
A shared network deployment needs explicit perimeter review; authentication alone
is not multi-user safety. TLS, access policy, firewalling and proxy session security
are operator responsibilities. The default Compose stack has no Valkey password
and is suitable only for its private network.

Provider URLs, AWS profiles, kubeconfigs, host keys, registry pins and executor
settings are trusted operator configuration. Never allow model output or uploaded
content to choose these or expand execution authority. Provider requests can leave
the host; verify destinations and forwarding/retention policies. Secret references
are persisted; resolved long-lived credentials stay in operator environment/files,
not AppSpec, database snapshots, queues, browser responses or GPU bundles.
AWS-managed runtimes use network isolation; direct GPU runtimes use their documented
backend-specific controls. Do not infer Docker restrictions for managed services.

Docker socket/group access, SSH accounts with Docker permissions and Kubernetes
exec RBAC are powerful administrative capabilities. Grant them only to dedicated
trusted processes. Protect staging directories and ownership journals from other
users, preserve them across restarts and impose filesystem/cluster quotas. Hashes
establish byte identity, not model correctness, licensing or freedom from all
malicious native-engine inputs. Custom remote model code/pickle is rejected; retain
that boundary. Review pinned engine/dependency updates before replacing images.

Playground requests are bounded and forwarded server-side only to verified owned
resources. Credentials stay off the browser. Chats are not persisted by the
playground, but training logs, imported data and artifacts can contain sensitive
content. Avoid recording request bodies in external proxies/APM. Object-store
signatures are bearer capabilities: protect their URLs and use short expiry.
See [safety and retention](safety-and-retention.md) for what deletion does and does
not remove. Backups need the same protection as live data.

Keep the reconciler running to enforce stop, cancellation and expiry. Failed API
access cannot prove deletion; pending cleanup may remain billable. Independently
verify cloud absence after incidents. Cost estimates are not billing guarantees.
Browser enhancements load pinned HTMX/Alpine from jsDelivr; this external script
supply boundary requires internet access and operator acceptance.

Accepted [ADRs](adr/) and versioned [contracts](contracts/) govern these boundaries.
Report suspected vulnerabilities privately to the repository maintainer through
an available private channel; do not put secrets, source payloads or exploitable
runtime details in a public issue.
