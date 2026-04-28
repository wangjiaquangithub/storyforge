# Stage 11 - Runtime Control Plane

## Goal

Broaden runtime claim visibility from per-project inspection into a lightweight control plane so operators can see active ownership, identify stale leases, and understand recovery targets before taking action.

## Completed

- Extended the store protocol with claim listing:
  - `list_project_claims()`
- Added claim listing support for:
  - in-memory store
  - SQLite store
- Added runtime helper:
  - `list_project_claims()`
- Added control-plane API:
  - `GET /api/runtime/claims`
- Claim list now exposes:
  - `project_id`
  - `worker_id`
  - `claimed_at`
  - `lease_expires_at`
  - `stale`
  - `seconds_until_expiry`
- Response also includes:
  - `total`
  - `stale_count`
- Claim list is sorted by `lease_expires_at` so the most urgent recovery targets appear first.
- Added tests for:
  - active vs stale claim visibility
  - list ordering by lease expiry
  - regression coverage for the existing operator claim endpoints

## Stage result

StoryForge now has a minimal runtime control plane instead of isolated claim lookups. Operators can inspect all current project ownership, immediately see which leases are stale, and recover with better visibility instead of debugging storage state by hand.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `23 passed`

## Not done yet

- bulk stale-claim recovery endpoint
- claim filtering by worker or staleness
- operator summaries for queued/running work per claimed project
- authenticated admin surface
- frontend/runtime dashboard on top of the control plane

## Next target

Stage 12 should add explicit stale-claim recovery actions at the control-plane level, especially a bulk-safe recovery path for expired leases.
