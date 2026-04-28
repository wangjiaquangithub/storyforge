# StoryForge v0.1 Boundary

## Objective

Version 0.1 exists to prove the new core semantics, not to prove feature breadth.

The only success condition is a clean first closed loop around:

`idea -> brief -> outline -> async chapter -> review/save`

## In scope

### 1. Core domain models
- Project
- Asset
- Config snapshot
- Task
- Event

### 2. Minimum execution semantics
- persisted task identity
- explicit task statuses
- explicit task events
- task state transitions
- project-scoped task ownership

### 3. Minimum backend surface
- health endpoint
- create/list project endpoints
- queue/list/get task endpoints
- task event endpoint
- explicit task transition endpoints for the first execution loop

### 4. First implementation target
The first real path should be:
1. create project from idea
2. queue brief generation task
3. produce and persist brief asset
4. queue outline generation task
5. produce and persist outline asset
6. queue chapter generation task
7. produce and persist chapter artifact
8. expose task progress and events throughout

## Explicitly out of scope for v0.1
- frontend application
- real websocket delivery
- distributed workers
- multi-provider model routing
- AI detection chain
- rewrite chain
- trend intelligence
- export system
- multi-user auth
- advanced collaboration
- benchmark-driven optimization

## Acceptance standard

v0.1 is acceptable only if:
- the new core is project-scoped
- long-running semantics are task-first
- task state is queryable
- events are queryable
- the first closed loop can be represented cleanly
- no global active-project semantics are introduced

## Non-goals

v0.1 is not trying to be feature-complete.
It is trying to make future feature work safe by establishing the right center of gravity.
