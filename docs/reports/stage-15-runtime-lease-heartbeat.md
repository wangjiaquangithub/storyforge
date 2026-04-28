# Stage 15 - Runtime Lease Heartbeat

## Goal

Keep long-running project execution from losing ownership purely because a fixed claim lease expires before background work finishes.

## Completed

- Extended `WorkerRuntime` with renewable lease timing state:
  - `_lease_seconds`
  - `_heartbeat_interval_seconds`
- Added claim renewal helper:
  - `_renew_project_claim()`
- Added background lease heartbeat helper:
  - `_start_claim_heartbeat()`
- Runtime execution paths now start lease renewal after successful claim acquisition:
  - `process_project_now()`
  - background `_loop()` project drain path
- Runtime shutdown/cleanup now releases claims narrowly through owned-claim checks:
  - `_release_owned_project_claim()`
- Claim release on task completion no longer blindly deletes whatever claim is present for the project; it only clears the claim if the current worker still owns it.
- Added tests for:
  - owned claim lease renewal over time
  - cleanup preserving a foreign takeover instead of deleting it accidentally
  - regression coverage for existing claim inspection, release, filtering, pagination, and audit controls

## Stage result

StoryForge runtime ownership is no longer tied to a single unrefreshed lease window. Long-running project drains can keep their claim alive while work is active, and runtime cleanup avoids deleting claims that have already been taken over by another worker.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_runtime.py -q`

Result:
- `23 passed`

## Not done yet

- explicit audit entries for lease renewal and lease loss
- configurable heartbeat settings at app/runtime construction time
- claim heartbeat visibility on the API surface
- recovery behavior when renewal fails mid-drain
- multi-worker coordination tests around takeover during an active heartbeat

## Next target

Stage 16 should expose runtime lease freshness more explicitly, especially heartbeat-aware inspection fields or operator-visible renewal status, so the control plane reflects active ownership health rather than only raw expiry timestamps.
