# Stage 10 - Claim Operator Surface

## Goal

Expose a minimal operator-facing control plane for project execution claims so stuck ownership can be inspected and, when necessary, explicitly cleared.

## Completed

- Added runtime inspection surface:
  - `GET /api/projects/{project_id}/runtime/claim`
- Added runtime recovery control:
  - `DELETE /api/projects/{project_id}/runtime/claim`
  - `DELETE /api/projects/{project_id}/runtime/claim?force=true`
- Added runtime helpers:
  - `get_project_claim()`
  - `release_project_claim()`
- Default release behavior is safe:
  - release succeeds only for this worker's own claim
  - foreign worker claim returns `409`
- Forced release allows operator override for stuck ownership.
- Added tests for:
  - claim inspection
  - foreign-owner release rejection
  - forced release success
  - regression coverage for existing runtime claim behavior

## Stage result

StoryForge now has a minimal operator control surface for runtime ownership. Claim state is no longer hidden inside storage internals; it can be queried and explicitly recovered when a lease becomes stuck or needs manual intervention.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py /Users/wangjiaquan/project/StoryForge/tests/test_recovery.py /Users/wangjiaquan/project/StoryForge/tests/test_observability.py -q`

Result:
- `21 passed`

## Not done yet

- claim listing across all active projects
- operator-visible lease age / remaining time summaries
- automatic stuck-claim recovery heuristics
- authenticated admin/operator permissions
- richer task/runtime dashboards on top of these controls

## Next target

Stage 11 should broaden this from per-project operator controls into a lightweight runtime control plane: list active claims, identify stale ownership, and offer safer bulk recovery visibility.
