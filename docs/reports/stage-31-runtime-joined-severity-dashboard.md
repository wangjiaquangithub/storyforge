# Stage 31 - Runtime Joined Severity Dashboard

## Goal

Help operators inspect the worst current runtime incidents in one place by joining worker and project severity rankings into a single ordered control-plane view.

## Completed

- Extended `GET /api/runtime/audits` to include `joined_severity_summary`.
- Added joined severity items that normalize worker and project urgency into one ranked list with:
  - `scope`
  - `project_id`
  - `worker_id`
  - `severity_score`
  - `stale_claims`
  - `heartbeat_overdue_claims`
  - `active_claims`
  - `recent_audit_count`
  - `lease_loss_count`
  - `forced_release_count`
  - `stale_release_count`
- Added `joined_severity_limit` so operators can cap the combined dashboard independently of raw audit pagination and the underlying worker/project severity lists.
- Joined severity now reuses the existing worker and project severity scoring surfaces instead of inventing a second scoring system.
- Added regression coverage for:
  - joined severity ordering on current claim-health-only scenarios
  - joined severity limiting behavior
  - mixed worker/project joined ranking across stale ownership and recent recovery noise
  - regression safety for existing severity summaries and preset behavior

## Stage result

StoryForge runtime audits now expose a single ranked incident dashboard instead of making operators mentally merge separate worker and project severity tables. The worst workers and worst projects can be scanned together using the same scoring slice.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `36 passed`

## Not done yet

- SSE updates for live joined severity changes
- richer joined dashboard metadata explaining why an item is ranked high
- incident snapshots for comparing joined dashboards over time
- dashboard filters that isolate claim-health versus recovery-noise incidents
- operator presets for joined triage views

## Next target

Stage 32 should add incident reason summaries so each ranked worker or project explains which signals are dominating its severity score.
