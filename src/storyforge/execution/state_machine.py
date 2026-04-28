from __future__ import annotations

from storyforge.domain.models import TaskRecord, TaskStatus, utc_now


class TaskStateMachine:
    _allowed_transitions: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.accepted: {TaskStatus.queued, TaskStatus.cancelled},
        TaskStatus.queued: {TaskStatus.running, TaskStatus.cancelled},
        TaskStatus.running: {TaskStatus.waiting_retry, TaskStatus.failed, TaskStatus.cancelled, TaskStatus.completed},
        TaskStatus.waiting_retry: {TaskStatus.queued, TaskStatus.failed, TaskStatus.cancelled},
        TaskStatus.failed: {TaskStatus.queued},
        TaskStatus.cancelled: {TaskStatus.queued},
        TaskStatus.completed: set(),
    }

    def can_transition(self, current: TaskStatus, target: TaskStatus) -> bool:
        return target in self._allowed_transitions[current]

    def transition(
        self,
        task: TaskRecord,
        target: TaskStatus,
        *,
        step: str,
        progress: float | None = None,
        error: str | None = None,
    ) -> TaskRecord:
        if not self.can_transition(task.status, target):
            raise ValueError(f"Invalid task transition: {task.status} -> {target}")
        task.status = target
        task.current_step = step
        if progress is not None:
            task.progress = progress
        task.error = error
        if target == TaskStatus.running and task.started_at is None:
            task.started_at = utc_now()
        if target in {TaskStatus.failed, TaskStatus.cancelled, TaskStatus.completed}:
            task.finished_at = utc_now()
        if target == TaskStatus.queued:
            task.finished_at = None
        return task
