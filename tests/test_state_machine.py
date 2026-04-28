import pytest

from storyforge.domain.models import TaskRecord, TaskStatus, TaskType
from storyforge.execution.state_machine import TaskStateMachine


def test_valid_task_transitions() -> None:
    machine = TaskStateMachine()
    task = TaskRecord(project_id="proj_test", task_type=TaskType.brief_generation)

    machine.transition(task, TaskStatus.queued, step="queued")
    machine.transition(task, TaskStatus.running, step="running")
    machine.transition(task, TaskStatus.completed, step="done", progress=1.0)

    assert task.status is TaskStatus.completed
    assert task.current_step == "done"
    assert task.progress == 1.0
    assert task.started_at is not None
    assert task.finished_at is not None


def test_invalid_transition_raises() -> None:
    machine = TaskStateMachine()
    task = TaskRecord(project_id="proj_test", task_type=TaskType.brief_generation)

    with pytest.raises(ValueError):
        machine.transition(task, TaskStatus.completed, step="done")
