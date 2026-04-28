# Stage 18 - Runtime Lease-Loss Summary

## Goal

Help operators understand whether lease-heartbeat failures are isolated or trending by exposing recent lease-loss summaries directly on the runtime audit control plane.

## Completed

- Added `LeaseLossSummaryResponse` to the API surface.
- Extended `GET /api/runtime/audits` to include `lease_loss_summary` alongside paged audit records.
- Lease-loss summary now exposes:
  - `recent_count`
  - `affected_projects`
  - `latest_created_at`
  - `latest_project_id`
- Summary is computed from the currently filtered audit result set, so operator filters narrow both the records and the summary context together.
- Added summary helper logic that aggregates `claim_heartbeat_lost` records without introducing a separate endpoint.
- Added tests for:
  - empty summary state when no lease-loss incidents are present
  - summary aggregation across multiple lease-loss incidents
  - filtered summary narrowing to a single project / action slice
  - regression coverage for existing audit filtering, pagination, and claim control-plane behavior

## Stage result

StoryForge audit queries now provide an at-a-glance lease-loss incident summary instead of requiring operators to manually count heartbeat-loss records from paged results. The runtime control plane can now show whether recent renewal failures are isolated or spread across multiple projects.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `26 passed`

## Not done yet

- time-windowed lease-loss summaries
- worker-grouped lease-loss summaries
- separate counters for deletion vs takeover heartbeat loss
- SSE delivery for summary changes
- top-level runtime health endpoint combining claims and audit summaries

## Next target

Stage 19 should add worker-focused runtime audit summaries so operators can quickly identify which workers are most associated with lease-loss and recovery activity.
