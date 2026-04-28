# Stage 07 - Replayable Event Streams

## Goal

Upgrade the live project event transport so reconnecting clients can resume from a known cursor instead of replaying the entire history or missing state transitions after a disconnect.

## Completed

- Extended project SSE endpoint:
  - `GET /api/projects/{project_id}/events`
- Added replayable cursor semantics via:
  - `after_event_id`
  - `after_timestamp`
  - standard `Last-Event-ID` header
- SSE payloads now include stream ids:
  - `id: <event_id>`
  - `event: <event_type>`
  - `data: <serialized EventRecord>`
- Backlog replay now sorts by `(timestamp, event_id)` before streaming.
- Live stream continues to use persisted task events as the source of truth.
- Added validation for malformed timestamp cursors with explicit `400` responses.
- Fixed a real SQLite concurrency issue discovered during regression by serializing connection access inside `SQLiteStoryForgeStore`.
- Added tests for:
  - base SSE event delivery with ids
  - resume via `after_event_id`
  - resume via `Last-Event-ID`
  - invalid timestamp rejection
  - runtime regression under background worker activity

## Stage result

StoryForge now has reconnect-friendly event streaming. A client can retain the last seen event id and resume from that point without reconsuming the whole project history, while the persisted event log remains authoritative.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `14 passed`

## Not done yet

- bounded replay windows / pagination for very large project histories
- named channel filtering beyond per-project stream scope
- multi-process live fanout beyond the current in-process event bus
- explicit heartbeat tuning and disconnect detection policies
- browser/frontend subscriber implementation

## Next target

Stage 08 should move from transport correctness into stronger queue/runtime guarantees, especially worker coordination and recovery under multi-threaded or multi-process execution.
