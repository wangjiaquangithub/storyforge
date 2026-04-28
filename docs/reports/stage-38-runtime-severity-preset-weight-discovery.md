# Stage 38 - Runtime Severity Preset Weight Discovery

## Goal

Expose each built-in runtime severity preset’s scoring weights from the discovery API so clients can inspect not only the preset shape but also the ranking logic it applies.

## Completed

- Added a dedicated discovery response shape for runtime severity presets.
- Extended `GET /api/runtime/severity-presets` so each preset now includes its `weights` bundle.
- Preset discovery responses now expose:
  - `name`
  - `window`
  - `severity_focus`
  - `worker_severity_limit`
  - `project_severity_limit`
  - `worker_summary_limit`
  - `project_summary_limit`
  - `action_summary_limit`
  - `weights`
- Kept `GET /api/runtime/audits` stable so its `severity_preset` field still reports only the active preset view metadata while `severity_weights` continues to describe the effective ranking weights for the current request.
- Added regression coverage verifying the discovery endpoint returns all built-in presets with the expected weight bundles.

## Stage result

StoryForge clients can now inspect the full scoring profile behind every named runtime severity preset directly from the control plane. Operator UIs and automation no longer need to guess which signals a preset emphasizes or duplicate those weight definitions out of band.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `38 passed`

## Not done yet

- add semantic preset labels such as ownership-heavy or recovery-heavy intent tags
- expose preset families or categories for grouped UI presentation
- support persisted custom presets beyond the built-in definitions
- add frontend discovery-driven preset selectors and preset detail popovers
- add capability metadata for future preset schema expansion

## Next target

Stage 39 should add semantic labels or categories to runtime severity preset discovery so clients can group presets by operator intent instead of only by preset name.
