# Stage 22 - Runtime Audit Time Windows

## Goal

Help operators narrow runtime audit history to recent operational windows by adding time-window controls to the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` with time-window query parameters:
  - `created_after`
  - `created_before`
- Time-window filters now apply to the currently filtered runtime audit result set before pagination.
- Lease-loss, worker, project, and action summaries now all derive from the same time-windowed audit slice as the raw records.
- Added ISO-8601 parsing and validation for audit time-window parameters.
- Added compatibility handling for query timestamps whose `+HH:MM` offset arrives URL-decoded as a space.
- Added tests for:
  - inclusive lower and upper time-window bounds
  - time-window narrowing across raw records and all summary surfaces together
  - invalid `created_after` / `created_before` values
  - reversed time-window rejection when `created_after` is later than `created_before`

## Stage result

StoryForge runtime audits can now answer not just "what has ever happened," but "what happened recently." Operators can constrain runtime recovery records and every derived summary to a recent incident window without losing consistency between summaries and raw audit rows.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `29 passed`

## Not done yet

- relative time-window shortcuts such as last hour / last day
- top-N summary limits for large retained audit histories
- time-windowed claim-health joins alongside audit summaries
- SSE updates for summary changes inside active windows
- operator presets for common incident-review windows

## Next target

Stage 23 should add top-N controls to runtime audit summaries so the control plane can cap large worker, project, and action aggregations without trimming the underlying audit record slice.
