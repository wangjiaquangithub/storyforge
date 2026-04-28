# CLAUDE.md

This file provides guidance to Claude Code when working in the StoryForge repository.

## Product definition

StoryForge is a project-based, asset-driven, controllable long-form novel production system for professional web novel authors and small content studios.

It is not primarily:
- a general chat assistant
- a prompt playground
- a single-chapter continuation toy

The product-critical path is:

`idea -> brief -> outline -> async chapter -> review/save`

If this chain is not working, the product is not working.

## Technical direction

StoryForge should be built around five first-class models:
- `Project`
- `Asset`
- `Config`
- `Task`
- `Event`

Core principles:
- project-scoped execution, never process-global active project semantics
- all long-running operations are asynchronous tasks
- task state is persisted and queryable
- websocket/event delivery is not the source of truth; persisted task/event state is
- asset data should support both structured canonical state and human-editable text overrides
- config should support system defaults, project overrides, and per-task frozen snapshots
- multi-agent execution is optional implementation detail; task graph and state machine are the actual backbone

## Scope discipline

The initial version should focus on one working closed loop only:
- idea intake
- brief generation
- outline generation
- chapter generation in background
- review / rewrite gate
- persistence and visibility in UI/API

Do not expand scope before this loop is solid.

## Build strategy

This repository is a fresh start for the new core.
Old InkFoundry code should be treated as reference material or migration source, not as an implementation constraint.

Preferred framing:
- rebuild the core cleanly
- only reuse old ideas/components if they still fit the new model
- do not preserve old behavior just because it existed before

## Collaboration preferences

- The user does not reopen old chats. Always leave restartable handoffs that work from a fresh window.
- Keep responses concise and direct.
- Do not create extra architecture or planning documents unless explicitly asked.
- Before writing substantial code, align on boundaries and model definitions.

## Near-term priorities

1. Define the new core v0.1 boundary.
2. Freeze the minimum domain models and task state machine.
3. Create the initial repository skeleton around the new core.
4. Only then begin implementing the first closed loop.
