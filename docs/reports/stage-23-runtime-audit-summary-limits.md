# Stage 23 - Runtime Audit Summary Limits

## Goal

Help operators keep runtime audit summaries readable on large retained histories by adding top-N controls to the runtime audit control plane.

## Completed

- Extended `GET /api/runtime/audits` with summary limit query parameters:
  - `worker_summary_limit`
  - `project_summary_limit`
  - `action_summary_limit`
- Worker, project, and action summaries now accept independent top-N limits without trimming the underlying raw audit record slice.
- Summary totals remain full-scope counts even when the returned summary item lists are truncated.
- Preserved existing summary ordering so top-N results stay deterministic and operator-friendly.
- Added tests for:
  - limiting worker, project, and action summary lengths independently
  - preserving raw audit `records` and `filtered_total` while summary lists are truncated
  - keeping aggregate `total_workers`, `total_projects`, and `total_actions` counts intact under truncation

## Stage result

StoryForge runtime audits can now summarize noisy operational history without flooding the control plane. Operators can keep the raw incident slice untouched while asking for only the most important workers, projects, or action types in the summary panels.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `30 passed`

## Not done yet

- named presets for common top-N views
- combined use of summary limits with relative time-window shortcuts
- summary pagination for deep audit exploration
- SSE updates that respect active summary limits
- joined claim-health ranking alongside limited audit summaries

## Next target

Stage 24 should add relative time-window shortcuts to runtime audits so operators can query recent incident windows like the last hour or last day without constructing explicit ISO timestamps.
