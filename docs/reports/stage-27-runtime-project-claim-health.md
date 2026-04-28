# Stage 27 - Runtime Project Claim Health Summaries

## Goal

Help operators identify which projects currently hold stale or heartbeat-overdue claims by adding per-project current claim-health summaries to the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` to include `project_claim_health_summary` alongside existing runtime audit summaries.
- Added per-project current claim-health fields:
  - `project_id`
  - `worker_id`
  - `stale`
  - `heartbeat_overdue`
  - `seconds_until_expiry`
- Added `project_claim_health_limit` so operators can cap per-project claim-health lists without trimming raw audit records or aggregate summaries.
- Project claim-health ordering now prioritizes the most urgent current project owners first:
  - stale projects before non-stale projects
  - then heartbeat-overdue projects before healthy ones
  - then the lowest `seconds_until_expiry`
- `project_id` filtering now narrows overall claim health, worker claim health, and project claim health to the same current project scope.
- Added tests for:
  - mixed project claim-health aggregation across healthy, stale, and overdue current claims
  - empty project claim-health summary when no current claims remain
  - project-scoped narrowing with `project_claim_health_limit`
  - regression coverage for existing runtime audit summaries and controls

## Stage result

StoryForge runtime audits now show not only which workers currently hold unhealthy claims, but which projects remain unhealthy right now. Operators can immediately see the most urgent project ownership problems without drilling into the full runtime claim list.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `33 passed`

## Not done yet

- joined ranking across worker health, project health, and recent audit activity
- SSE updates for project claim-health changes
- project claim-health presets combined with time windows and summary limits
- operator dashboards for unhealthy projects and active recovery noise
- richer severity scoring for project ownership health

## Next target

Stage 28 should add combined severity ranking across current claim health and recent runtime audit activity so operators can prioritize the noisiest and most unhealthy workers or projects first.
