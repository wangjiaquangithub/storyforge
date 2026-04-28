# Stage 12 - Bulk Stale Claim Recovery

## Goal

Add a control-plane recovery path that can safely clear expired runtime claims in bulk without touching active ownership.

## Completed

- Added runtime helper:
  - `release_stale_project_claims()`
- Added control-plane recovery API:
  - `DELETE /api/runtime/claims/stale`
- Bulk recovery behavior is intentionally narrow:
  - only expired claims are released
  - active claims remain untouched
- Added response model with:
  - `released_project_ids`
  - `released_count`
- Added tests for:
  - mixed active + stale claim recovery
  - no-op behavior when no claims are stale
  - regression coverage for existing runtime claim listing and per-project recovery controls

## Stage result

StoryForge now has a safe bulk recovery path for stuck runtime ownership. Operators can clear expired claims across the control plane without manually deleting them one by one and without risking live worker ownership.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `25 passed`

## Not done yet

- dry-run mode for bulk recovery
- claim filtering for targeted bulk recovery by worker or project set
- automatic re-enqueue after stale claim cleanup
- audit/event trail for operator recovery actions
- authenticated operator permissions around bulk control-plane actions

## Next target

Stage 13 should add recovery audit visibility so claim releases and bulk operator actions show up in a durable operator-facing event history.
