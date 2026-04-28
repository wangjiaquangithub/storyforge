# Stage 16 - Runtime Lease Observability

## Goal

Expose heartbeat-aware lease freshness on the runtime control plane so operators can distinguish healthy active ownership from stale or heartbeat-lagged claims.

## Completed

- Extended `ProjectExecutionClaim` with lease freshness metadata:
  - `lease_duration_seconds`
  - `heartbeat_interval_seconds`
  - `last_heartbeat_at`
- Extended runtime claim acquisition and renewal so claim records persist heartbeat metadata on every save.
- Extended runtime claim response payloads with heartbeat-aware fields:
  - `lease_duration_seconds`
  - `heartbeat_interval_seconds`
  - `last_heartbeat_at`
  - `heartbeat_age_seconds`
  - `heartbeat_overdue`
- Added claim response mapping helper so per-project claim inspection and control-plane claim listing use the same freshness semantics.
- Extended `GET /api/runtime/claims` with optional filtering by:
  - `heartbeat_overdue`
- `GET /api/projects/{project_id}/runtime/claim` now returns the same heartbeat-aware claim shape used by the claim list surface.
- Added tests for:
  - per-project claim inspection exposing heartbeat freshness metadata
  - control-plane claim filtering by overdue heartbeat state
  - regression coverage for existing claim filtering, pagination, renewal, and audit behavior

## Stage result

StoryForge runtime claims now expose whether a lease is merely unexpired or actively healthy. Operators can inspect heartbeat timing directly and filter the claim list down to overdue leases before they fully expire.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `23 passed`

## Not done yet

- lease freshness fields on audit records
- operator-facing freshness summaries across the whole runtime
- audit entries for missed heartbeats or lost ownership
- configurable overdue thresholds independent of heartbeat interval
- SSE delivery for claim freshness changes

## Next target

Stage 17 should make runtime recovery more explicit around heartbeat loss, especially audit-visible lease-loss or renewal-failure records that explain why ownership changed or became unsafe.
