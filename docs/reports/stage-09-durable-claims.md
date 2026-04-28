# Stage 09 - Durable Worker Claims

## Goal

Move runtime coordination beyond in-process locks by introducing persisted worker claims so project execution ownership can survive disconnects, restarts, and stale worker state.

## Completed

- Added `ProjectExecutionClaim` to the domain model.
- Extended the store protocol with durable project claim operations:
  - `save_project_claim()`
  - `get_project_claim()`
  - `delete_project_claim()`
- Added in-memory claim storage for test/runtime parity.
- Added SQLite-backed `project_claims` table.
- `WorkerRuntime` now has a stable `worker_id` for claim ownership.
- Added claim acquisition before runtime execution:
  - active foreign claim blocks execution
  - expired claim can be reclaimed
  - owned claim can be refreshed/replaced by the same worker
- Runtime now releases claims after successful or failed drain completion.
- `process-now` respects the same claim gate as the background worker.
- Added tests for:
  - active foreign claim blocking execution
  - expired claim reclamation
  - existing runtime coordination regressions

## Stage result

StoryForge now has a minimal durable execution-ownership layer. Runtime execution is no longer protected only by in-memory coordination; claim state is persisted and can prevent duplicate project execution even across process boundaries, as long as workers respect the same claim protocol.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `18 passed`

## Not done yet

- claim renewal during long-running execution
- crash recovery that explicitly requeues projects left half-drained
- claim inspection APIs for operators
- worker heartbeats and lease monitoring
- stronger task-level ownership beyond project-scoped claims

## Next target

Stage 10 should add claim introspection and explicit recovery controls so operators can see which worker owns a project and safely recover from stuck leases.
