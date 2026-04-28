# Stage 34 - Runtime Severity Focus Filtering

## Goal

Help operators isolate incidents dominated by claim-health signals versus recovery-noise signals without manually reading every ranked worker, project, and joined severity item.

## Completed

- Extended `GET /api/runtime/audits` with `severity_focus` query filtering.
- Added supported focus values:
  - `claim_health`
  - `recovery_noise`
- Added server-side validation for invalid focus values.
- Added contribution-aware focus matching based on weighted severity contributions.
- Claim-health focus now keeps only severity items where claim-health contribution totals strictly dominate recovery-noise contribution totals.
- Recovery-noise focus now keeps only severity items where recovery-noise contribution totals strictly dominate claim-health contribution totals.
- Focus filtering is applied consistently across:
  - `worker_severity_summary`
  - `project_severity_summary`
  - `joined_severity_summary`
- Existing score calculation, reason summaries, presets, pagination, and audit record filtering remain unchanged.
- Added regression coverage for:
  - claim-health-dominant filtering
  - recovery-noise-dominant filtering
  - joined summary propagation under focus filtering
  - invalid focus rejection
  - mixed incidents that should move between focus views based on weighted dominance

## Stage result

StoryForge runtime severity views can now be sliced by operational cause. Operators can switch between ownership-health incidents and recovery-churn incidents directly at the control-plane API instead of manually interpreting every contribution list.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `37 passed`

## Not done yet

- top-level focus distribution summaries before filtering
- focus-aware named presets for common triage modes
- live SSE updates when an incident changes dominant focus
- focus trend snapshots across time windows
- UI controls that expose focus filters directly in operator dashboards

## Next target

Stage 35 should add severity focus summaries so operators can see how many worker, project, and joined incidents are currently claim-health-dominant versus recovery-noise-dominant before applying filters.
