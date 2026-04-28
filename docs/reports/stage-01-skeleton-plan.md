# Stage 01 - Skeleton Plan

## Goal

Turn the product and technical definitions into a minimal implementation skeleton that makes the new architectural center explicit.

## Planned deliverables

- Python package and dependency configuration
- first-class domain models
- task state machine
- in-memory repository/store layer
- minimal FastAPI app
- minimal tests for project/task/event flow
- explicit v0.1 scope document
- process and stage reporting conventions

## Why this stage matters

The old system failed partly because execution semantics, scope, and state ownership were muddy.
This stage fixes that by making the new assumptions concrete before feature work starts.

## Exit criteria

Stage 01 exits when:
- the new repository has a coherent package layout
- the core models are codified
- the task-first API surface exists
- tests cover the first model and state-machine assumptions
- the next stage can focus on implementing the first real closed loop instead of repository bootstrapping
