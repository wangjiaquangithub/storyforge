# Stage 28 - Runtime Severity Ranking

## Goal

Help operators prioritize the worst current runtime problems by combining current claim-health severity with recent runtime audit noise into unified worker and project rankings.

## Completed

- Extended `GET /api/runtime/audits` to include:
  - `worker_severity_summary`
  - `project_severity_summary`
- Added worker severity ranking fields:
  - `worker_id`
  - `severity_score`
  - `stale_claims`
  - `heartbeat_overdue_claims`
  - `active_claims`
  - `recent_audit_count`
  - `lease_loss_count`
  - `forced_release_count`
  - `stale_release_count`
- Added project severity ranking fields:
  - `project_id`
  - `worker_id`
  - `severity_score`
  - `stale_claim`
  - `heartbeat_overdue`
  - `recent_audit_count`
  - `lease_loss_count`
  - `forced_release_count`
  - `stale_release_count`
- Added independent top-N controls:
  - `worker_severity_limit`
  - `project_severity_limit`
- Worker severity now combines current unhealthy claim ownership with recent recovery activity using a weighted score that prioritizes:
  - stale claims
  - heartbeat-overdue claims
  - lease-loss audit activity
  - forced and stale-release audit activity
  - overall recent audit count
- Project severity now combines current project claim health with recent project audit activity under the same control-plane slice.
- Added tests for:
  - combined severity ordering across current unhealthy claims and recent audit noise
  - scoped severity narrowing with project filters and severity limits
  - empty severity summaries when no current claims or matching recent audits remain
  - regression coverage for existing runtime audit summaries and controls

## Stage result

StoryForge runtime audits now do more than list unhealthy state and noisy history separately. Operators can immediately rank which workers and projects are currently the most urgent based on both current ownership health and recent recovery churn.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `33 passed`

## Not done yet

- configurable severity weights for different operator priorities
- SSE updates for live severity ranking changes
- severity presets that combine time windows and top-N controls
- joined dashboards that group unhealthy workers and projects together
- persistent incident snapshots for comparing severity over time

## Next target

Stage 29 should add configurable severity weights so operators can tune whether stale claims, overdue heartbeats, or recent audit churn dominate the combined rankings.
