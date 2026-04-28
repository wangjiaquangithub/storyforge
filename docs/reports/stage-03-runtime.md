# Stage 03 - Runtime Separation

## Goal

Separate transport, application orchestration, and worker execution so StoryForge task processing no longer depends purely on direct synchronous drain calls in request handlers.

## Completed

- Added an application service layer for project-level orchestration actions.
- Added a worker runtime abstraction with:
  - background queue ingestion
  - background worker thread
  - direct immediate processing path
- Moved first-loop execution entry points through the application layer.
- Added runtime endpoints for:
  - project enqueue to background runtime
  - immediate runtime processing
- Kept explicit manual worker endpoints for inspection and controlled execution.
- Added tests for:
  - inline auto-run first loop
  - background runtime processing through queued project execution

## Stage result

StoryForge now has a real execution boundary:
- API layer handles transport
- application layer handles orchestration intent
- workflow layer handles task graph execution
- runtime layer handles background processing
- persistence layer stores durable state

This is still a lightweight single-process runtime, but the architecture is now much closer to the final intended shape.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_state_machine.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `8 passed`

## Not done yet

- durable worker lease/locking semantics
- task cancellation and retry orchestration
- streaming progress delivery
- real model-backed generation
- richer review and rewrite gates
- frontend control surface

## Next target

Stage 04 should introduce explicit task retry/cancel controls and richer runtime-safe execution semantics so the system can recover from failure without manual DB-level intervention.
