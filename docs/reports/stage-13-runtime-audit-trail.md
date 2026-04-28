# Stage 13 - Runtime Audit Trail

## Goal

Make operator recovery actions durable and queryable so claim releases are not invisible control-plane mutations.

## Completed

- Added `RuntimeAuditRecord` to the domain model.
- Extended the store protocol with:
  - `append_runtime_audit()`
  - `list_runtime_audits()`
- Added in-memory runtime audit storage.
- Added SQLite-backed `runtime_audits` table.
- Runtime now records durable audit entries for:
  - forced single-project claim release
  - bulk stale-claim release
- Added audit query API:
  - `GET /api/runtime/audits`
  - supports optional `project_id` filtering
- Audit records expose:
  - `audit_id`
  - `action`
  - `project_id`
  - `actor_worker_id`
  - `claim_worker_id`
  - `forced`
  - `stale`
  - `message`
  - `created_at`
- Added tests for:
  - forced claim release audit persistence
  - bulk stale release audit persistence
  - regression coverage for existing runtime recovery controls

## Stage result

StoryForge now has an operator-facing audit trail for recovery activity. Claim releases are no longer silent storage edits; they are durable runtime records that can be queried later for debugging, incident review, and operational accountability.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `27 passed`

## Not done yet

- audit entries for successful claim acquisition / renewal
- pagination or limits for large audit histories
- richer operator attribution beyond worker id
- audit streaming over SSE
- frontend operator timeline built on top of the audit log

## Next target

Stage 14 should add filtering and paging controls around runtime audits and claims so the control plane stays usable as operational history grows.
