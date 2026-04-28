# Stage 36 - Runtime Focus-Aware Presets

## Goal

Let operators request reusable runtime severity views that bundle contribution focus with windows, limits, and weights in one named preset.

## Completed

- Extended `SeverityPresetDefinition` with `severity_focus`.
- Extended the `severity_preset` response payload returned by `GET /api/runtime/audits` with `severity_focus`.
- Updated the built-in presets so:
  - `balanced` keeps no focus filter
  - `claim_health` automatically applies `severity_focus=claim_health`
  - `recovery_noise` automatically applies `severity_focus=recovery_noise`
- Preset-derived focus is now applied automatically when callers provide `severity_preset` without an explicit `severity_focus` override.
- Explicit `severity_focus` query parameters still override the preset’s bundled focus when operators need a one-off view.
- Added regression coverage for:
  - preset response payloads including bundled focus
  - recovery-noise preset narrowing worker/project/joined severity views to recovery-noise-dominant incidents
  - explicit focus override on top of a bundled preset
  - regression safety for preset-specific weight overrides

## Stage result

StoryForge severity presets now define complete triage slices instead of only ranking math. Operators can request a named mode and immediately get the matching audit window, ranking weights, list limits, and contribution-dominance filter together.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `37 passed`

## Not done yet

- expose preset metadata from a dedicated discovery endpoint
- add more operator presets for mixed or escalation-heavy incident views
- allow custom saved presets persisted outside the built-in definitions
- add frontend preset controls and preset badges on runtime audit dashboards
- combine focus-aware presets with historical trend or bucket summaries

## Next target

Stage 37 should expose available runtime severity presets as an explicit API surface so operator clients can discover preset names, bundled focus, and ranking defaults without hardcoding them.
