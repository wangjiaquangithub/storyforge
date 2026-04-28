# StoryForge Process Log

## Working mode

Claude should continue working autonomously until interrupted, while keeping this repository updated with:
- process documentation
- stage result documentation
- stage reports
- overall summaries when meaningful milestones are reached

## Current build sequence

1. Reset old code path and create fresh repository.
2. Write repository context (`CLAUDE.md`).
3. Write product and technical definitions.
4. Define v0.1 boundary and build the first core skeleton.
5. Implement the first closed loop incrementally.

## Current stage

Stage 0 complete: repository foundation and initial new-core skeleton.
Stage 1 complete: first closed loop implemented on the new semantic model.
Stage 2 complete: SQLite persistence introduced for projects, assets, tasks, and events.
Stage 3 complete: runtime separation introduced between API, application orchestration, workflow, and worker execution.
Stage 4 complete: task recovery semantics added for retry and cancellation flows.
Stage 5 complete: formal project/task observability surface introduced.
Stage 6 complete: live project-scoped event delivery introduced over SSE.
Stage 7 complete: replayable SSE cursors and reconnect semantics introduced.
Stage 8 complete: project-scoped runtime coordination and duplicate enqueue suppression introduced.
Stage 9 complete: durable worker claims introduced for recoverable project ownership.
Stage 10 complete: operator-facing claim inspection and recovery controls introduced.
Stage 11 complete: runtime claim listing and stale-lease visibility introduced.
Stage 12 complete: bulk stale-claim recovery introduced.
Stage 13 complete: runtime recovery audit trail introduced.
Stage 14 complete: runtime query filtering and pagination introduced for claims and audits.
Stage 15 complete: renewable runtime claim leases introduced for long-running execution.
Stage 16 complete: heartbeat-aware lease freshness observability introduced for runtime claims.
Stage 17 complete: durable lease-loss runtime audits introduced for heartbeat interruption.
Stage 18 complete: lease-loss incident summaries introduced on the runtime audit control plane.
Stage 19 complete: worker-focused runtime audit summaries introduced.
Stage 20 complete: project-focused runtime audit summaries introduced.
Stage 21 complete: action-focused runtime audit summaries introduced.
Stage 22 complete: time-windowed runtime audit filtering introduced for records and summaries.
Stage 23 complete: top-N runtime audit summary controls introduced.
Stage 24 complete: relative runtime audit window shortcuts introduced.
Stage 25 complete: current claim health joined into runtime audit summaries.
Stage 26 complete: per-worker current claim health summaries introduced.
Stage 27 complete: per-project current claim health summaries introduced.
Stage 28 complete: combined severity ranking introduced across current claim health and recent runtime audit activity.
Stage 29 complete: configurable severity weights introduced for runtime severity ranking.
Stage 30 complete: named runtime severity presets introduced for reusable ranking modes.
Stage 31 complete: joined runtime severity dashboard introduced across workers and projects.
Stage 32 complete: severity reason summaries introduced for worker, project, and joined runtime rankings.
Stage 33 complete: severity contribution breakdowns introduced for worker, project, and joined runtime rankings.
Stage 34 complete: contribution-aware severity focus filtering introduced for worker, project, and joined runtime rankings.
Stage 35 complete: severity focus summaries introduced for worker, project, and joined runtime rankings.
Stage 36 complete: focus-aware severity presets introduced for reusable triage views.
Stage 37 complete: runtime severity preset discovery API introduced.
Stage 38 complete: runtime severity preset discovery now exposes scoring weights.
Stage 39 complete: runtime severity preset discovery now exposes semantic categories and labels.

## Latest progress

- Added durable repo context and definition files.
- Created the first Python package skeleton.
- Added Project / Asset / Config / Task / Event domain models.
- Added task state-machine rules.
- Added a real first closed loop service for brief -> outline -> chapter -> review.
- Added SQLite persistence as the default app store.
- Verified persistence across app restart with automated tests.
- Added application service and worker runtime layers.
- Added background runtime processing and runtime-focused tests.
- Added retry and cancellation semantics with recovery-focused tests.
- Added project execution summary APIs for observability.
- Added live project-scoped SSE event delivery backed by persisted task events.
- Added replayable SSE cursors plus SQLite thread-safety for background/runtime concurrency.
- Added project-scoped runtime coordination and duplicate enqueue suppression.
- Added durable worker claims for recoverable project execution ownership.
- Added operator-facing claim inspection and recovery APIs.
- Added runtime claim listing with stale-lease visibility.
- Added bulk stale-claim recovery at the runtime control-plane level.
- Added durable runtime audit records for recovery actions.
- Added runtime claim/audit filtering and pagination controls.
- Added renewable runtime claim leases for long-running execution.
- Added heartbeat-aware lease freshness observability for runtime claims.
- Added durable lease-loss runtime audits for heartbeat interruption.
- Added lease-loss incident summaries on the runtime audit control plane.
- Added worker-focused runtime audit summaries.
- Added project-focused runtime audit summaries.
- Added action-focused runtime audit summaries.
- Added time-windowed runtime audit filtering for records and summaries.
- Added top-N runtime audit summary controls.
- Added relative runtime audit window shortcuts.
- Added current claim health alongside runtime audit summaries.
- Added per-worker current claim health summaries.
- Added per-project current claim health summaries.
- Added combined severity ranking across current claim health and recent runtime audit activity.
- Added configurable severity weights for runtime severity ranking.
- Added named runtime severity presets for reusable ranking modes.
- Added joined runtime severity dashboard across workers and projects.
- Added severity reason summaries for worker, project, and joined runtime rankings.
- Added severity contribution breakdowns for worker, project, and joined runtime rankings.
- Added contribution-aware severity focus filtering for worker, project, and joined runtime rankings.
- Added severity focus summaries for worker, project, and joined runtime rankings.
- Added focus-aware severity presets for reusable claim-health and recovery-noise triage views.
- Added runtime severity preset discovery APIs for operator clients.
- Added preset weight bundles to runtime severity preset discovery.
- Added semantic categories and labels to runtime severity preset discovery.
- Added stage reports and v0.1 boundary documentation.
- Created a local virtual environment and verified the current test suite successfully.

## Expected artifact pattern

For each meaningful stage:
- one short process update in `docs/PROCESS.md`
- one stage result file under `docs/reports/`
- code and tests matching the stage scope
