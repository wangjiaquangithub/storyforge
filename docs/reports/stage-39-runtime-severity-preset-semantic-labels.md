# Stage 39 - Runtime Severity Preset Semantic Labels

## Goal

Add semantic categories and labels to runtime severity preset discovery so clients can group preset modes by operator intent instead of relying only on preset names.

## Completed

- Extended runtime severity preset definitions with semantic metadata.
- Extended `GET /api/runtime/severity-presets` so each preset now exposes:
  - `category`
  - `labels`
- Added semantic grouping metadata for the built-in presets:
  - `balanced` → category `general`
  - `claim_health` → category `ownership`
  - `recovery_noise` → category `recovery`
- Added operator-facing labels for each built-in preset so clients can group or badge presets without maintaining their own mapping.
- Kept `GET /api/runtime/audits` stable so the active request shape remains unchanged while discovery carries the richer metadata.
- Added regression coverage verifying the discovery endpoint returns the expected categories and labels for all built-in presets.

## Stage result

StoryForge preset discovery now carries intent-level metadata alongside technical configuration. Operator UIs can present grouped preset pickers, badges, or filter chips without duplicating preset semantics outside the control plane.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `38 passed`

## Not done yet

- add multi-category or weighted taxonomy support for more complex grouping
- expose preset ordering or display priority metadata
- support persisted custom presets with semantic metadata
- add frontend grouped preset pickers and badges driven by discovery data
- add capability metadata for clients that need to detect schema growth safely

## Next target

Stage 40 should add explicit display ordering metadata to runtime severity preset discovery so clients can render preset groups and presets in a stable operator-friendly order.
