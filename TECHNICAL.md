# StoryForge Technical Definition

## 1. One-line technical identity

StoryForge should not be built as a web page that directly calls a model and waits for text.
It should be built as a project-scoped, task-driven, asset-first, event-observable long-form content production platform.

## 2. Four technical planes

### 2.1 Control plane
Responsible for:
- project management
- configuration management
- asset management
- task submission
- task inspection
- manual intervention

It should issue commands and expose state, not do the actual chapter production inline.

### 2.2 Execution plane
Responsible for:
- queueing
- worker execution
- multi-step pipeline orchestration
- retry / cancel / resume
- concurrency control

This plane generates content.

### 2.3 Feedback plane
Responsible for:
- event streaming
- progress updates
- error exposure
- step-level logs and traces
- client subscription

WebSocket is only a delivery channel, not the source of truth.
The source of truth must be persisted task and event state.

### 2.4 Persistence plane
Responsible for storing:
- project metadata
- config snapshots
- project assets
- outlines
- chapters
- task records
- QA results
- versions and rollback points

## 3. Five first-class models

### 3.1 Project model
A project is the top-level namespace.
Every task, asset, config, chapter, and event belongs to one project.

Minimum fields:
- `project_id`
- `idea`
- `brief`
- `genre / tags / audience`
- `target_length`
- `status`
- `current_phase`
- `active_config_profile`
- `active_asset_version`
- `latest_outline_version`
- `latest_chapter_cursor`

Rule: never use process-global active project semantics as the real model.

### 3.2 Asset model
Long-form generation requires persistent project assets, not just prompts.

Asset categories include:
- brief
- world
- characters
- factions / power system / rules
- timeline
- writing_rules
- style_prompt
- outline
- chapter summaries
- continuity notes

Minimum fields:
- `asset_type`
- `project_id`
- `version`
- `source`
- `content`
- `structured_data`
- `updated_at`

Rule: keep both structured canonical state and human-editable text overrides.

### 3.3 Config model
Configuration must have three layers:
1. system defaults
2. project overrides
3. per-task frozen effective snapshots

The frozen task snapshot is essential for reproducibility, reruns, debugging, and auditability.

Config domains include:
- provider / base_url / api key reference
- model selection
- prompt/profile selection
- chapter length policy
- review policy
- AI detect policy
- rewrite policy
- retry policy
- concurrency policy

### 3.4 Task model
Tasks are the actual backbone of the system.
All long-running work must be modeled as persisted tasks.

Minimum fields:
- `task_id`
- `project_id`
- `task_type`
- `status`
- `priority`
- `payload`
- `effective_config_snapshot`
- `input_asset_refs`
- `output_refs`
- `parent_task_id`
- `retry_count`
- `current_step`
- `progress`
- `error`
- `created_at / started_at / finished_at`

Representative task types:
- brief_generation
- asset_bootstrap
- outline_generation
- chapter_plan
- chapter_generation
- chapter_review
- ai_detection
- rewrite
- export

Rule: task graph first, multi-agent second.
Agents are replaceable execution nodes; tasks and state transitions are the real architecture.

### 3.5 Event model
Events are formal data, not loose logs.
They are jointly consumed by backend, UI, and operators.

Minimum fields:
- `event_id`
- `project_id`
- `task_id`
- `event_type`
- `step`
- `message`
- `progress`
- `payload`
- `timestamp`

Representative event types:
- accepted
- queued
- started
- step_started
- step_progress
- step_completed
- waiting_retry
- blocked
- failed
- cancelled
- completed

Rule: the UI should render task and event state, not infer progress from request timing.

## 4. Execution chain

The main execution chain should be represented as a task graph, not a single giant function.

### Stage A: project initiation
`idea -> brief generation`

### Stage B: asset bootstrap
`brief -> world / characters / rules / timeline`

### Stage C: structure generation
`assets -> outline`

### Stage D: chapter production
`chapter summary + context + assets -> draft`

### Stage E: quality control
`draft -> review -> continuity check -> ai detect -> optional rewrite`

### Stage F: persistence and export readiness
`final text -> save -> version -> export candidate`

Every stage must use explicit input refs and output refs.
No critical state should exist only in transient memory.

## 5. Concurrency and isolation rules

### Rule 1: cross-project concurrency, same-project controlled serialization
Different projects may run in parallel.
Tasks that mutate the same project-critical state should be serialized or gated.

### Rule 2: tasks must survive process restarts
At minimum, task records, current state, failure state, and recent checkpoints must persist.

### Rule 3: failures must be visible
Failures cannot live only in logs.
Users must see where the pipeline failed, what the error was, whether it will retry, and whether it can be rerun manually.

## 6. Storage shape

The ideal storage shape includes three layers:

### 6.1 Relational storage
For:
- project metadata
- task tables
- event index
- config
- chapter index
- asset index
- version records

### 6.2 Asset/file storage
For:
- editable markdown rule files
- exported drafts
- intermediate snapshots
- reviewable text artifacts

### 6.3 Retrieval/memory storage
For:
- chapter-summary embeddings
- character context retrieval
- world-knowledge recall

Rule: retrieval stores are supporting layers only, never the sole source of truth.

## 7. System laws

1. All long tasks are asynchronous.
2. Project context is request/task scoped, never process-global by design.
3. Every execution freezes its effective inputs.
4. Steps should be idempotent or near-idempotent.
5. Every failure enters the formal state machine.
6. The frontend submits tasks, subscribes to events, and queries status; it must not depend on model runtime duration.

## 8. Multi-agent technical stance

Multi-agent orchestration can exist, but it is not the primary architecture.
Planner, writer, reviewer, detector, and rewriter are execution roles.
The real architecture is:
- task graph
- state machine
- explicit inputs and outputs
- quality gates
- retry and recovery behavior

## 9. v1 minimum technical capabilities

Version 1 requires at least:
1. persisted task queue
2. project-level isolation
3. layered config with task snapshots
4. asset versioning
5. chapter production state machine
6. event stream plus status query
7. retry and manual rerun
8. same-project serialization and cross-project concurrency
9. QA gates
10. human override mechanisms

## 10. What v1 does not need first

Not first priority:
- full microservice decomposition
- distributed scheduler complexity
- collaboration/social features
- multi-tenant enterprise auth
- agent marketplace concepts
- overbuilt cost optimization
- large trend-commercialization loops

A sensible v1 is:
- one control plane
- one execution plane
- persisted task storage
- project asset layer
- one working closed loop

## 11. Final technical definition

StoryForge should be engineered around five first-class models:
- Project
- Asset
- Config
- Task
- Event

If these five models are correct, the product can grow cleanly.
If they are not, everything else becomes a patch over weak semantics.
