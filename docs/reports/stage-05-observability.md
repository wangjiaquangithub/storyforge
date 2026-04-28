# Stage 05 - Observability Surface

## Goal

Expose a formal inspection surface so clients can understand project execution state, failure conditions, and task backlog without reconstructing everything from raw task rows and event logs.

## Completed

- Added project execution summary models.
- Added application-level project summary computation.
- Added project summary API:
  - `GET /api/projects/{project_id}/summary`
- Summary now exposes:
  - task totals by status
  - failed task ids
  - waiting-retry task ids
  - latest known error by task
  - latest event type/message
  - latest task involved in execution activity
- Added tests for:
  - successful completed pipeline summary
  - retryable failure summary visibility

## Stage result

StoryForge now has an actual observability surface instead of forcing clients to replay all task and event records manually. This makes it possible for a frontend or control plane to present execution health, backlog state, and recovery opportunities with one stable query.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_state_machine.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `12 passed`

## Not done yet

- websocket/push event delivery
- task filtering/pagination/query endpoints beyond current basic scope
- richer project queue metrics
- real model-backed generation
- frontend control surface

## Next target

Stage 06 should introduce live event delivery or polling-friendly status channels so external clients can react to task progress without only using manual summary refreshes.
