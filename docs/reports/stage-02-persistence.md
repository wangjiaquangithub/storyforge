# Stage 02 - Persistence Layer

## Goal

Replace in-memory-only assumptions with a real persistent storage layer while preserving the task-first and project-scoped semantics established in Stage 01.

## Completed

- Introduced a store protocol to formalize repository expectations.
- Added a SQLite-backed persistent store for:
  - projects
  - assets
  - tasks
  - events
- Switched the default app store from in-memory to SQLite-backed persistence.
- Preserved in-memory store support for fast isolated tests.
- Updated the workflow so task transitions are explicitly saved back to storage.
- Added a restart-persistence test proving that projects, tasks, assets, and events survive app recreation.
- Replaced deprecated FastAPI shutdown hook usage with lifespan handling.

## Stage result

StoryForge is no longer only a process-memory demo. The new core now has a durable state substrate that can survive process restarts while preserving the same semantic model:
- project-scoped ownership
- task-first execution
- persisted events
- persisted assets
- persisted tasks

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_state_machine.py -q`

Result:
- `6 passed`

## Not done yet

- decoupled repository and service modules beyond the current store abstraction
- true background worker runtime
- richer retry/cancel orchestration
- model-backed brief/outline/chapter generation
- websocket/event streaming delivery
- frontend control surface

## Next target

Stage 03 should separate orchestration from transport more cleanly and introduce a real worker loop so task processing is no longer triggered only by synchronous API calls.
