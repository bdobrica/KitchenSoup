# ADR 0014: PostgreSQL-authoritative training dispatch and reconciliation

Status: Accepted

Date: 2026-10-19

## Context

Milestone 13 moves long-running execution out of web requests. ADR 0001 deferred
transition/event enforcement, and ADR 0011 explicitly deferred queues, claims and
recovery. Submitted plans, pinned images, local Docker isolation and retained
workspaces must keep their meaning across process restarts.

## Decision

Supersede ADR 0011's synchronous materialization/submission and on-request status
updates. Creating a run commits QUEUED and its deterministic external identity
before publishing a Valkey wakeup; the existing API retains its request/response
shape and 201 response. GET reads PostgreSQL without Docker observation. Worker
and reconciler are independent processes, explicitly enabled for host training.
The default Compose services keep Docker access disabled.

Use the normalized states in PLAN section 25. PostgreSQL validates insertion and
transitions and appends each state event in the same transaction, including the
initial QUEUED event. Same-state writes emit no duplicate event. Existing rows
receive a current-state baseline if needed, without reconstructing unknown history.
Existing snapshot and event immutability guards remain. Python validates the same
transition matrix for service errors; database guards also cover direct SQL.

The worker scans QUEUED, PREPARING and CANCEL_REQUESTED runs; the reconciler scans
SUBMITTED, RUNNING and POSTPROCESSING runs. Both take a per-run PostgreSQL
transaction advisory claim before external effects, using a separate connection
from short row-locked state transactions. Claims have no expiring lease and release
on process/connection death. This claim also coordinates terminal cleanup. Local
workspace locks and deterministic Docker identities remain a second safeguard.

Valkey holds bounded UUID-only command hints and publishes transient UUID updates.
Neither a queue message nor a publication authorizes execution or supplies run
configuration. Periodic database scans recover lost wakeups, consumed messages,
queue deletion and Valkey outages. Duplicate delivery cannot resubmit an attempt.
No new queue dependency or workflow engine is introduced.

Recovery adopts an existing verified container after a PREPARING crash. A missing
container at that stage fails the abandoned attempt; a created but unstarted
container is stopped and the attempt fails. Preparation/start are never replayed
on the same workspace. Docker unavailability leaves state unchanged for polling.
Confirmed absence after submission becomes LOST. Verified success passes through
RUNNING and POSTPROCESSING to SUCCEEDED, recording every edge even for fast jobs.
POSTPROCESSING completes executor output verification here; registry import remains
Milestone 14. Terminal states are never overwritten by later observations.

Cancellation before dispatch moves QUEUED directly to CANCELED. To support a
request during materialization, extend PLAN's graph with PREPARING to
CANCEL_REQUESTED; submission checks the row again before recording SUBMITTED.
The worker subsequently stops the verified container. CANCEL_REQUESTED may end
in CANCELED, FAILED or LOST; verified completion that wins the stop race moves to
POSTPROCESSING and SUCCEEDED. POSTPROCESSING finishes without a new cancel request.
A retry is an explicit terminal-run clone, with a new UUID/workspace/events and
identical retained inputs/image. It does not re-resolve current recipes.

SSE replays ordered PostgreSQL events with sequence IDs and Last-Event-ID, sends
current database snapshots, and uses Valkey publications as wakeups. Periodic reads
repair missed publications; bounded connections reconnect. Logs retain the existing
bounded cursor-based private endpoint and text rendering. No credentials, model
inputs, commands or logs are placed in queue/publication messages.

## Consequences

Run creation and cancellation return durable pending state promptly. Clients must
handle normalized intermediate states; the optional observation field remains
available for compatibility but is no longer populated by GET. Host web, worker
and reconciler share one stable private workspace, database, Valkey and daemon.
They run under the same non-root operator. No socket is granted to the ordinary
Compose web/worker/reconciler containers. Image resolution remains a short host
Docker inspection before creating a run.

A crashed preparation is recoverable by explicit clone, not blind replay. There is
no automatic retry, GPU capacity scheduler, artifact registration, retention engine
or multi-user authority model in this milestone. Durable event history, restart,
claims, cancellation races, executor status mapping and transient Valkey behavior
are verified against disposable services without requiring a GPU.
