# Stage 29 - Runtime Severity Weights

## Goal

Let operators tune runtime severity ranking so current stale claims, overdue heartbeats, and recent audit churn can be weighted differently for their operating priorities.

## Completed

- Extended `GET /api/runtime/audits` to accept configurable severity weight controls:
  - `stale_claim_weight`
  - `heartbeat_overdue_weight`
  - `lease_loss_weight`
  - `forced_release_weight`
  - `stale_release_weight`
  - `recent_audit_weight`
- Added `severity_weights` to the runtime audit response so callers can see the effective ranking inputs that shaped the current summaries.
- Refactored worker severity scoring to use the effective severity weights instead of hardcoded coefficients.
- Refactored project severity scoring to use the same effective severity weights.
- Preserved the previous default scoring behavior by keeping the same default weight values:
  - stale claims: `100`
  - heartbeat overdue claims: `50`
  - lease-loss audits: `20`
  - forced-release audits: `10`
  - stale-release audits: `5`
  - general recent audit noise: `1`
- Added regression coverage for:
  - default severity weight echoing
  - custom severity weight echoing
  - worker severity ranking reordering when operator priorities change
  - project severity ranking reordering when operator priorities change

## Stage result

StoryForge runtime severity ranking is no longer fixed to one opinionated formula. Operators can now retune ranking behavior per request and immediately see whether current claim-health incidents or recent recovery churn should dominate worker and project urgency ordering.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `34 passed`

## Not done yet

- named severity presets that bundle weights with windows and summary limits
- SSE updates for live severity ranking changes
- joined dashboards that group the worst workers and projects together
- persistent incident snapshots for comparing severity across time slices
- operator-facing guidance for choosing ranking modes

## Next target

Stage 30 should add named severity presets so operators can switch between urgency modes without manually setting every weight and summary control on each request.
