# Stage 25 - Runtime Claim Health Summary Join

## Goal

Help operators see current claim health alongside recent runtime recovery history by joining current claim-state aggregation into the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` to include `claim_health_summary` alongside raw audit records and existing derived summaries.
- Added claim-health aggregation fields:
  - `total_claims`
  - `stale_claims`
  - `heartbeat_overdue_claims`
  - `healthy_claims`
  - `affected_projects`
  - `affected_workers`
- Claim health is computed from the current runtime claim surface, not retained audit history.
- `project_id` filtering now narrows both runtime audit history and current claim-health aggregation to the same project scope.
- Added tests for:
  - mixed healthy, stale, and heartbeat-overdue current claims
  - empty claim-health summary when no current claims remain
  - project-scoped claim-health narrowing through the runtime audit endpoint
  - regression coverage for existing audit filtering, time windows, and summary surfaces

## Stage result

StoryForge runtime audits now show both what recently went wrong and what is currently unhealthy. Operators can inspect recovery noise and immediately see whether there are still stale or heartbeat-overdue claims active on the runtime surface.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `33 passed`

## Not done yet

- per-worker claim-health breakdowns alongside worker audit summaries
- time-window-aware comparisons between current claim health and recent audit slices
- summary ranking that combines claim-health severity with audit activity
- SSE updates for joined claim-health summary changes
- operator dashboard presets for unhealthy claim triage

## Next target

Stage 26 should add per-worker claim-health summaries so operators can see which workers currently own stale or heartbeat-overdue claims without drilling into the full claim list.
