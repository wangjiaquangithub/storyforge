# Stage 30 - Runtime Severity Presets

## Goal

Give operators reusable severity-ranking modes so they can switch control-plane views without manually setting every time window, summary limit, and weight parameter on each request.

## Completed

- Extended `GET /api/runtime/audits` to accept `severity_preset`.
- Added named severity presets:
  - `balanced`
  - `claim_health`
  - `recovery_noise`
- Each preset now carries a reusable bundle of:
  - default relative window
  - worker/project severity top-N controls
  - worker/project/action audit summary top-N controls
  - severity weight defaults
- Added `severity_preset` to the runtime audit response so callers can inspect the active preset metadata.
- Preset defaults are applied first, while explicit query parameters still override preset values for fine-grained tuning.
- Preset validation now rejects unknown preset names with a clear `400` response.
- Added regression coverage for:
  - preset metadata echoing
  - preset-driven weight and window application
  - preset-driven severity reordering
  - explicit query parameter overrides on top of a preset
  - invalid preset rejection

## Stage result

StoryForge runtime audits now support reusable operator modes instead of forcing manual tuning every time. Operators can jump between balanced ranking, claim-health-heavy ranking, and recovery-noise-heavy ranking while still retaining exact override control when needed.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `35 passed`

## Not done yet

- SSE updates for live severity ranking changes
- preset catalogs for joined worker/project dashboards
- persistent incident snapshots for comparing ranked views over time
- operator annotations describing why a preset should be used
- separate presets for incident triage versus historical review

## Next target

Stage 31 should add joined severity dashboards so operators can inspect the worst workers and projects together in a single ranked control-plane view.
