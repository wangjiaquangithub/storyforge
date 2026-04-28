# Stage 35 - Runtime Severity Focus Summaries

## Goal

Help operators see how many current severity incidents are claim-health-dominant versus recovery-noise-dominant before applying focus filters.

## Completed

- Added `worker_severity_focus_summary` to `GET /api/runtime/audits`.
- Added `project_severity_focus_summary` to `GET /api/runtime/audits`.
- Added `joined_severity_focus_summary` to `GET /api/runtime/audits`.
- Added shared focus-summary aggregation over severity contribution breakdowns.
- Focus summaries now classify incidents into:
  - `claim_health_dominant`
  - `recovery_noise_dominant`
  - `mixed_or_neutral`
  - `total_items`
- Worker focus summaries count dominance across worker severity items.
- Project focus summaries count dominance across project severity items.
- Joined focus summaries count dominance across the combined worker/project severity dashboard.
- Focus summaries respect active `severity_focus` filtering and severity limits so operators can see the distribution of the currently visible severity slice.
- Added regression coverage for:
  - mixed stale-claim versus recovery-noise distributions
  - filtered claim-health-only focus views
  - filtered recovery-noise-only focus views
  - empty-result focus summaries on actor-scoped audit filtering
  - regression safety for existing severity ranking and focus filtering behavior

## Stage result

StoryForge runtime severity APIs now expose the shape of the current incident set before operators drill into individual ranked items. They can immediately tell whether the visible queue is dominated by unhealthy ownership, recovery churn, or mixed cases.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `37 passed`

## Not done yet

- focus summaries split by severity bucket or preset
- percentage and trend overlays for focus distributions
- focus summaries over historical snapshots instead of only current views
- SSE updates when focus distributions change live
- frontend controls and charts for operator dashboards

## Next target

Stage 36 should add focus-aware presets so operators can request reusable triage views that bundle windows, limits, weights, and contribution focus in one named mode.
