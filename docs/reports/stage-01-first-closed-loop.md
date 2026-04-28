# Stage 01 - First Closed Loop

## Goal

Prove that the new StoryForge core can represent and execute the first end-to-end closed loop using the right semantics.

Closed loop target:
`idea -> brief -> outline -> async chapter -> review/save`

## Completed

- Added a closed-loop workflow service.
- Added task dependency sequencing across:
  - brief generation
  - outline generation
  - chapter generation
  - chapter review
- Added automatic asset production for:
  - brief
  - outline
  - chapter
  - review note
- Added project progression updates during loop execution.
- Added project asset listing and latest-asset retrieval endpoints.
- Added a first-loop queue endpoint.
- Added a queue-drain worker endpoint to simulate asynchronous processing semantics.
- Expanded tests to cover the real first closed loop.
- Verified the test suite in the repository virtual environment.

## Stage result

The new repository now proves the right architectural center:
- project-scoped tasks
- explicit task dependencies
- task-first execution semantics
- asset persistence at each meaningful output step
- queryable task and event history

Even though execution is still in-memory and worker simulation is simple, the semantic shape is now correct.

## Verified

Command run:

`/Users/wangjiaquan/project/StoryForge/.venv/bin/python -m pytest /Users/wangjiaquan/project/StoryForge/tests/test_app.py /Users/wangjiaquan/project/StoryForge/tests/test_state_machine.py -q`

Result:
- `5 passed`

## Not done yet

- durable persistence instead of in-memory storage
- real background worker runtime
- model-backed content generation
- review/rewrite quality gates beyond a placeholder note
- frontend control surface

## Next target

Stage 02 should introduce persistent repository/storage boundaries so the task-first core survives process restarts and becomes the real basis for future work.
