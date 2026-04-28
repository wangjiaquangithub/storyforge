# Stage 08 - Runtime Coordination Guarantees

## Goal

Strengthen the runtime so the same project is not drained concurrently by multiple execution paths, while preserving concurrency across different projects.

## Completed

- Added per-project execution locking inside `ClosedLoopService`.
- `process_next()` and `drain()` now serialize execution for the same project.
- Added a dedicated unlocked internal drain path so project coordination is enforced in one place.
- Added runtime enqueue deduplication inside `WorkerRuntime`.
- Repeated `enqueue_project(project_id)` calls no longer create duplicate queued drains for the same project.
- Background worker now clears project queue membership after the drain completes.
- Preserved cross-project concurrency by using project-scoped coordination instead of a global execution lock.
- Added tests for:
  - duplicate background enqueue suppression
  - `process-now` coordination against an already enqueued background drain
  - regression coverage for existing runtime and SSE behavior

## Stage result

StoryForge now has a stronger single-process runtime guarantee: one project executes serially even if multiple control paths try to run it, while different projects are still free to progress independently.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `16 passed`

## Not done yet

- lease-based worker ownership across multiple processes
- persisted queue claims / recovery after worker crash mid-drain
- cancellation of in-flight running work
- queue prioritization and fairness policies
- explicit runtime conflict responses at the API layer

## Next target

Stage 09 should move from in-process coordination to recoverable worker claims so runtime safety survives process restarts and multi-worker deployment.
