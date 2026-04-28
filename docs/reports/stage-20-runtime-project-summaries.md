# Stage 20 - Runtime Project Summaries

## Goal

Help operators see which projects are generating the most runtime recovery noise by adding project-focused summaries to the runtime audit control plane.

## Completed

- Added project summary response models:
  - `ProjectAuditSummaryItem`
  - `ProjectAuditSummaryResponse`
- Extended `GET /api/runtime/audits` to include `project_summary` alongside paged audit records, lease-loss summaries, and worker summaries.
- Project summaries now aggregate by `project_id` and expose:
  - `project_id`
  - `total_count`
  - `lease_loss_count`
  - `forced_release_count`
  - `stale_release_count`
- Summary ordering prioritizes projects with the most overall runtime audit activity, then lease-loss volume.
- Project summary is computed from the currently filtered audit result set, so operator filters narrow project aggregations and raw records together.
- Added tests for:
  - empty project summary when no filtered records remain
  - project summary alongside existing audit filtering and pagination behavior
  - single-project summary narrowing when filters isolate one project
  - multi-project aggregation across lease-loss, forced release, and stale-release activity

## Stage result

StoryForge runtime audits now show not only which workers are noisy, but which projects are driving recovery activity. Operators can quickly identify the most problematic projects before drilling into individual lease-loss or release records.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `27 passed`

## Not done yet

- time-windowed project summaries
- combining current claim state with project audit summaries
- top-N summary limits for large audit histories
- project summary SSE updates
- joining project summaries with operator-facing health scores

## Next target

Stage 21 should add action-focused runtime audit summaries so the control plane can quickly show which recovery action types are dominating current operational history.
