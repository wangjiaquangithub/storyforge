# Stage 24 - Runtime Audit Relative Windows

## Goal

Help operators query recent runtime audit history quickly by adding relative incident-window shortcuts to the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` with a relative window query parameter:
  - `window`
- Added supported relative window shortcuts:
  - `last_hour`
  - `last_day`
  - `last_week`
- Relative windows now narrow raw audit records and every derived summary surface together.
- Added conflict validation so `window` cannot be combined with explicit `created_after` / `created_before` bounds.
- Added validation for unsupported relative window values.
- Added tests for:
  - `last_day` narrowing to recent multi-record slices
  - `last_hour` narrowing to only the freshest audit slice
  - invalid `window` rejection
  - conflict rejection when mixing relative and explicit time bounds

## Stage result

StoryForge runtime audits can now answer common incident-review questions with a single parameter instead of hand-built ISO timestamps. Operators can quickly inspect the last hour, day, or week while keeping raw records and all summary surfaces aligned.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `32 passed`

## Not done yet

- custom relative windows such as rolling 15-minute or 6-hour views
- named operator presets that combine relative windows with summary limits
- SSE updates scoped to active relative windows
- dashboard endpoints that join relative audit slices with current claim health
- timezone-aware operator preference presets for incident review

## Next target

Stage 25 should join current claim health with runtime audit summaries so operators can see recent recovery noise alongside the current stale or overdue claim surface in one control-plane response.
