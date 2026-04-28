# Stage 32 - Runtime Severity Reasons

## Goal

Help operators understand why a worker or project ranks highly by surfacing the dominant claim-health or audit-noise reasons behind each severity score.

## Completed

- Extended worker severity items to include `severity_reasons`.
- Extended project severity items to include `severity_reasons`.
- Extended joined severity items to include `severity_reasons`.
- Worker severity reasons now summarize the strongest current unhealthy ownership and recent recovery signals, including:
  - stale claims
  - heartbeat-overdue claims
  - lease-loss audits
  - forced-release audits
  - stale-release audits
  - fallback recent-audit noise when that is the only active contributor
- Project severity reasons now summarize the strongest project-specific signals, including:
  - stale current claim
  - heartbeat-overdue current claim
  - lease-loss audits
  - forced-release audits
  - stale-release audits
  - fallback recent-audit noise when that is the only active contributor
- Joined severity now reuses the same worker/project explanation strings instead of inventing a separate explanation layer.
- Added regression coverage for:
  - worker reason summaries on stale and heartbeat-overdue current claims
  - project reason summaries on stale and heartbeat-overdue current claims
  - joined severity reason propagation
  - filtered severity reason behavior under scoped project views
  - regression safety for existing severity ranking, presets, and joined dashboard behavior

## Stage result

StoryForge runtime rankings now explain themselves. Operators can see not just that a worker or project is urgent, but whether the urgency is being driven by stale ownership, overdue heartbeats, or repeated recovery churn.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `36 passed`

## Not done yet

- SSE updates for live severity reason changes
- quantitative reason contributions instead of text-only summaries
- historical reason snapshots for incident comparisons
- operator hinting for which remediation action best matches a dominant reason
- grouped reasons across related workers and projects in the same incident cluster

## Next target

Stage 33 should add severity reason contribution details so operators can see not only the dominant reason labels but the weighted signal breakdown behind each ranked item.
