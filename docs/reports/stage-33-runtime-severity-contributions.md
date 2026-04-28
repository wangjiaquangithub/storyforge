# Stage 33 - Runtime Severity Contributions

## Goal

Help operators see the weighted signal breakdown behind each worker or project severity score instead of only the final score and top reason labels.

## Completed

- Extended worker severity items to include `severity_contributions`.
- Extended project severity items to include `severity_contributions`.
- Extended joined severity items to include `severity_contributions`.
- Added a shared contribution builder that records, per active signal:
  - `signal`
  - `count`
  - `weight`
  - `contribution`
- Worker severity contributions now expose weighted breakdowns across:
  - stale claims
  - heartbeat-overdue claims
  - lease-loss audits
  - forced-release audits
  - stale-release audits
  - recent audit noise
- Project severity contributions now expose the same weighted breakdowns using project-level claim health semantics.
- Joined severity now reuses the same worker/project contribution lists instead of computing a separate breakdown layer.
- Added regression coverage for:
  - worker contribution breakdowns on stale and heartbeat-overdue current claims
  - project contribution breakdowns on stale and heartbeat-overdue current claims
  - joined contribution propagation
  - contribution ordering across mixed stale-claim and forced-release scenarios
  - regression safety for existing severity ranking, presets, reason summaries, and joined dashboard behavior

## Stage result

StoryForge runtime rankings now show not only which workers and projects are most urgent, but exactly how much each unhealthy claim or recent recovery signal is contributing to the final severity score.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `36 passed`

## Not done yet

- SSE updates for live severity contribution changes
- percentage-style contribution views alongside raw weighted totals
- grouped contribution rollups across related workers and projects in the same incident cluster
- historical contribution snapshots for comparing severity shifts over time
- operator hints that map dominant contributions to likely remediation actions

## Next target

Stage 34 should add contribution-aware filtering so operators can isolate incidents dominated by claim-health signals versus recovery-noise signals without manually reading every ranked item.
