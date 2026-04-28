# Stage 14 - Runtime Query Controls

## Goal

Keep the operator control plane usable as runtime history grows by adding filtering and pagination to claim and audit queries.

## Completed

- Extended `GET /api/runtime/claims` with optional query controls:
  - `worker_id`
  - `project_id`
  - `stale`
  - `offset`
  - `limit`
- Extended `GET /api/runtime/audits` with optional query controls:
  - `project_id`
  - `action`
  - `actor_worker_id`
  - `forced`
  - `stale`
  - `offset`
  - `limit`
- Claim list responses now expose:
  - `total`
  - `filtered_total`
  - `offset`
  - `limit`
  - `stale_count`
- Audit list responses now expose:
  - `total`
  - `filtered_total`
  - `offset`
  - `limit`
- Kept existing ordering semantics intact:
  - claims remain ordered by `lease_expires_at`
  - audits remain ordered by `created_at` descending
- Added tests for:
  - claim filtering by `worker_id`, `project_id`, and `stale`
  - claim pagination metadata and slicing behavior
  - audit filtering by `action`, `project_id`, `forced`, `stale`, and `actor_worker_id`
  - audit pagination metadata and slicing behavior
  - regression coverage for existing runtime claim and audit endpoints

## Stage result

StoryForge now has a queryable runtime control plane instead of an all-or-nothing dump. Operators can narrow runtime state to the projects, workers, and recovery actions they care about, and can page through larger result sets without losing overall visibility.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `21 passed`

## Not done yet

- time-range filtering for audit history
- pagination links or cursors for operator clients
- richer claim-side filters such as expiry windows
- audit streaming over SSE
- authenticated operator access around runtime control-plane queries

## Next target

Stage 15 should add claim lease renewal and heartbeat semantics so long-running project execution does not rely on a fixed lease window without refresh.
