# Stage 00 - Foundation

## Goal

Create the durable context and minimum code skeleton needed to start StoryForge as a fresh new-core repository.

## Completed

- Initialized the new Git repository.
- Added `CLAUDE.md` with product-critical direction and collaboration rules.
- Added `PRODUCT.md` and `TECHNICAL.md` as durable definition files.
- Created the initial Python package structure under `src/storyforge/`.
- Added first-class domain models for Project, Asset, Config, Task, and Event.
- Added a minimal task state machine.
- Added an in-memory store for projects, tasks, assets, and events.
- Added a minimal FastAPI app exposing health, project creation, task queueing, task inspection, and task state transitions.

## Stage result

StoryForge now has a runnable conceptual skeleton for the new core, with the correct architectural center of gravity:
- project-scoped state
- task-first execution semantics
- event visibility
- explicit domain models

## Not done yet

- persistent storage
- real websocket delivery
- real model-backed generation
- rewrite gates beyond a minimal review note
- frontend

## Next target

Stage 01 should solidify repository/service boundaries and evolve the in-memory closed loop into a persistent execution path.
