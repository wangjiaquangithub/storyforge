# Stage 17 - Runtime Lease-Loss Audit

## Goal

Make heartbeat interruption visible in the runtime audit trail so operators can see when a worker loses claim renewal continuity, not just when a claim is manually released or bulk-recovered.

## Completed

- Added runtime helper:
  - `_record_claim_renewal_loss()`
- Heartbeat renewal now records a durable runtime audit when renewal stops because:
  - the claim no longer exists
  - the claim moved to another worker
- Added new runtime audit action:
  - `claim_heartbeat_lost`
- Lease-loss audit records include:
  - `project_id`
  - `actor_worker_id`
  - `claim_worker_id` when ownership moved to another worker
  - `stale`
  - descriptive `message`
- Kept existing renewal behavior unchanged when the current worker still owns the claim.
- Added tests for:
  - heartbeat loss after claim deletion
  - heartbeat loss after foreign-worker takeover
  - regression coverage for existing claim listing, claim release, stale recovery, and audit query behavior

## Stage result

StoryForge runtime audits now explain why a heartbeat stopped instead of silently ending claim renewal. Operators can query control-plane history and see whether ownership disappeared entirely or shifted to a different worker.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `25 passed`

## Not done yet

- separate audit actions for claim deletion vs claim takeover
- API-level summaries for recent heartbeat-loss incidents
- automatic recovery behavior after renewal loss
- SSE delivery for lease-loss audit events
- operator attribution beyond runtime worker id

## Next target

Stage 18 should add control-plane summaries for recent lease-loss incidents so operators can quickly see whether heartbeat problems are isolated or becoming systemic.
