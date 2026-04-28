# Stage 19 - Runtime Worker Summaries

## Goal

Help operators identify which workers are associated with recent runtime recovery activity by adding worker-focused summaries to the runtime audit control plane.

## Completed

- Added worker summary response models:
  - `WorkerAuditSummaryItem`
  - `WorkerAuditSummaryResponse`
- Extended `GET /api/runtime/audits` to include `worker_summary` alongside paged audit records and lease-loss summaries.
- Worker summaries now aggregate by `actor_worker_id` and expose:
  - `worker_id`
  - `total_count`
  - `lease_loss_count`
  - `forced_release_count`
  - `stale_release_count`
- Summary ordering prioritizes workers with the most overall runtime audit activity, then lease-loss volume.
- Worker summary is computed from the currently filtered audit result set, so operator filters narrow worker aggregations and raw records together.
- Added tests for:
  - worker summary alongside existing audit filtering and pagination behavior
  - empty worker summary when no filtered records remain
  - multi-worker aggregation across lease-loss, forced release, and stale-release activity
  - filtered worker summary narrowing to a single actor slice

## Stage result

StoryForge runtime audits now show not only what recovery activity happened, but which workers are most associated with it. Operators can quickly identify whether lease-loss or recovery activity is concentrated around a specific worker before digging into individual records.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `27 passed`

## Not done yet

- time-windowed worker summaries
- per-worker project fanout counts
- separate takeover-vs-deletion lease-loss counts per worker
- joining worker summary with current active claims
- SSE delivery for worker summary changes

## Next target

Stage 20 should add project-focused runtime audit summaries so the control plane can quickly show which projects are generating the most recovery noise.
