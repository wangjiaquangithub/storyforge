# Stage 06 - Live Event Delivery

## Goal

Expose a live project-scoped event stream so clients can react to queued, running, retry, failure, and completion transitions without polling task records for every state change.

## Completed

- Added a lightweight in-process `EventBus` for project-scoped subscribers.
- Wired `ClosedLoopService.record_event()` so persisted task events are also published live.
- Routed task events emitted by:
  - queue acceptance/queuing
  - task execution start/completion/failure
  - retry requests
  - cancellation requests
  - manual task start/complete/fail endpoints
- Added project SSE endpoint:
  - `GET /api/projects/{project_id}/events`
- SSE stream now emits:
  - `event: <event_type>`
  - `data: <serialized EventRecord>`
- Kept persisted event storage as the source of truth; live delivery is a transport layer on top.
- Added tests for:
  - existing background runtime behavior
  - live project event stream delivery

## Stage result

StoryForge now has a minimal live status channel for project execution. Clients can subscribe once per project and receive task lifecycle events as they happen, while durable event history remains queryable from the store.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `11 passed`

## Not done yet

- websocket transport alongside SSE
- replay / cursor semantics for reconnecting clients
- heartbeat / backpressure tuning beyond the minimal keep-alive
- cross-process event delivery beyond the current in-process runtime
- richer project-wide filtering beyond per-project subscription

## Next target

Stage 07 should strengthen the live execution surface with reconnect-friendly replay semantics or broaden it into a richer control-plane transport.
