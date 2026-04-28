# Stage 21 - Runtime Action Summaries

## Goal

Help operators understand which runtime recovery action types are dominating operational history by adding action-focused summaries to the runtime audit control plane.

## Completed

- Added action summary response models:
  - `ActionAuditSummaryItem`
  - `ActionAuditSummaryResponse`
- Extended `GET /api/runtime/audits` to include `action_summary` alongside paged audit records, lease-loss summaries, worker summaries, and project summaries.
- Action summaries now aggregate by `action` and expose:
  - `action`
  - `total_count`
  - `forced_count`
  - `stale_count`
- Summary ordering prioritizes the most frequent action types in the currently filtered audit result set.
- Action summary is computed from the currently filtered audit result set, so operator filters narrow action aggregations and raw records together.
- Added tests for:
  - empty action summary when no filtered records remain
  - single-action summary narrowing when filters isolate one action slice
  - multi-action aggregation across lease-loss, forced release, and stale-release activity
  - regression coverage for existing lease-loss, worker, and project summary surfaces

## Stage result

StoryForge runtime audits now show not only which workers and projects are noisy, but which action types are driving that noise. Operators can quickly tell whether the control plane is dominated by heartbeat loss, forced releases, or stale-claim recovery before drilling into individual records.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `27 passed`

## Not done yet

- time-windowed action summaries
- combining action summaries with current claim health
- top-N action summary limits for large audit histories
- action summary SSE updates
- grouped recovery dashboards that join action, worker, and project signals

## Next target

Stage 22 should add time-window controls to the runtime audit control plane so operators can narrow summaries and records to recent operational history instead of always querying the full retained audit set.
