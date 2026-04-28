# Stage 26 - Runtime Worker Claim Health Summaries

## Goal

Help operators identify which workers currently own stale or heartbeat-overdue claims by adding per-worker current claim-health summaries to the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` to include `worker_claim_health_summary` alongside existing runtime audit summaries.
- Added per-worker current claim-health aggregation fields:
  - `worker_id`
  - `total_claims`
  - `stale_claims`
  - `heartbeat_overdue_claims`
  - `healthy_claims`
  - `affected_projects`
- Added `worker_claim_health_limit` so operators can cap per-worker claim-health lists without trimming raw audit records or the aggregate claim-health summary.
- Worker claim-health ordering now prioritizes the most urgent current owners first:
  - highest `stale_claims`
  - then highest `heartbeat_overdue_claims`
  - then highest `total_claims`
- `project_id` filtering now narrows both overall claim health and per-worker claim health to the same current project scope.
- Added tests for:
  - mixed worker claim-health aggregation across healthy, stale, and overdue current claims
  - empty worker claim-health summary when no current claims remain
  - project-scoped narrowing with `worker_claim_health_limit`
  - regression coverage for existing runtime audit summaries and controls

## Stage result

StoryForge runtime audits can now show not only whether the current claim surface is unhealthy, but exactly which workers currently own that unhealthy state. Operators can prioritize the workers holding stale or overdue claims before drilling into the full claim list.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `33 passed`

## Not done yet

- per-project current claim-health summaries alongside project audit summaries
- combined ranking across current claim-health severity and recent audit activity
- SSE updates for per-worker claim-health changes
- worker health presets that combine time windows and claim-health limits
- joined operator dashboards for unhealthy workers and noisy recovery history

## Next target

Stage 27 should add per-project current claim-health summaries so operators can see which projects still hold unhealthy claims alongside the existing project-focused runtime audit summaries.
