# Stage 37 - Runtime Severity Preset Discovery

## Goal

Let operator clients discover available runtime severity presets from the API instead of hardcoding preset names and bundled defaults.

## Completed

- Added `GET /api/runtime/severity-presets`.
- Added `SeverityPresetListResponse` for enumerating preset definitions.
- Reused the existing preset response shape so discovery and audit responses expose the same fields.
- Discovery payloads now include, for each preset:
  - `name`
  - `window`
  - `severity_focus`
  - `worker_severity_limit`
  - `project_severity_limit`
  - `worker_summary_limit`
  - `project_summary_limit`
  - `action_summary_limit`
- Added regression coverage verifying the endpoint returns all built-in presets in a stable response shape.
- Preset discovery now exposes:
  - `balanced`
  - `claim_health`
  - `recovery_noise`

## Stage result

StoryForge runtime operators and UI clients can now query the control plane for the currently available preset modes and their bundled triage behavior. Clients no longer need to duplicate preset names or assume focus/window defaults out of band.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `38 passed`

## Not done yet

- expose preset weight bundles from the discovery endpoint
- tag presets by operator intent or severity family
- support custom persisted presets in addition to built-ins
- add frontend preset pickers driven directly by discovery responses
- add versioning or capability flags for future preset schema growth

## Next target

Stage 38 should expose preset weight bundles in the discovery API so clients can inspect not only the view shape but also the scoring logic behind each preset.
