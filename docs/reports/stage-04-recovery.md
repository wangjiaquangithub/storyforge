# Stage 04 - Recovery Semantics

## Goal

Introduce explicit retry and cancellation semantics so task execution can recover from non-happy-path states without requiring ad hoc manual intervention.

## Completed

- Expanded task-state transitions to support re-queuing from failed and cancelled states.
- Added application-level retry control for retryable tasks.
- Added application-level cancellation control for queued/non-terminal tasks.
- Added workflow retry behavior based on task-level retry limits.
- Added retry events and cancellation events to the persisted event stream.
- Added API endpoints for:
  - task retry
  - task cancel
- Added automated tests covering:
  - queued task cancellation
  - waiting-retry transition
  - re-queue after retry
  - terminal failure after retry limit is exhausted

## Stage result

StoryForge now has the first real recovery surface for task execution. It is no longer limited to happy-path queueing and completion. The runtime can now represent and control:
- retryable failure
- explicit re-queue
- safe queued-task cancellation
- terminal failure after retry budget is exhausted

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_state_machine.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py -q`

Result:
- `10 passed`

## Not done yet

- safe cancellation of in-flight running work
- lease/locking semantics for multi-worker coordination
- push-based event streaming
- richer task inspection and filtering
- real model-backed generation
- frontend control surface

## Next target

Stage 05 should introduce task inspection/summary endpoints and richer status surfaces so external clients can understand project execution health without reconstructing it from raw tables.
