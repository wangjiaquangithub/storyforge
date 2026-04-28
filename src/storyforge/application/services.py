from __future__ import annotations

from pydantic import BaseModel, Field

from storyforge.domain.models import AssetType, EventRecord, EventType, TaskRecord, TaskStatus, TaskType
from storyforge.execution.workflow import ClosedLoopService, DrainResult
from storyforge.llm import LlmUsage


def _coerce_usage_count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _coerce_chapter_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.isdigit():
            chapter_number = int(stripped)
            return chapter_number if chapter_number > 0 else None
    return None


class TaskSummary(BaseModel):
    total: int = 0
    accepted: int = 0
    queued: int = 0
    running: int = 0
    waiting_retry: int = 0
    failed: int = 0
    cancelled: int = 0
    completed: int = 0


class FailureSummary(BaseModel):
    failed_task_ids: list[str] = Field(default_factory=list)
    waiting_retry_task_ids: list[str] = Field(default_factory=list)
    last_error_by_task: dict[str, str] = Field(default_factory=dict)


class QualityExperimentSummary(BaseModel):
    experiment: str
    variant: str
    review_count: int = 0
    approved_count: int = 0
    rewrite_count: int = 0
    issue_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ProjectExecutionSummary(BaseModel):
    project_id: str
    task_summary: TaskSummary
    failure_summary: FailureSummary
    latest_task_id: str | None = None
    latest_event_type: str | None = None
    latest_event_message: str | None = None
    llm_usage: LlmUsage = Field(default_factory=LlmUsage)
    quality_experiments: list[QualityExperimentSummary] = Field(default_factory=list)


class ProjectApplicationService:
    def __init__(self, workflow: ClosedLoopService) -> None:
        self.workflow = workflow

    def queue_first_loop(self, project_id: str, *, chapter_number: int = 1, branch: str = "main") -> list[TaskRecord]:
        return self.workflow.queue_first_loop(project_id, chapter_number=chapter_number, branch=branch)

    def process_next(self, project_id: str, *, branch: str | None = None) -> TaskRecord | None:
        return self.workflow.process_next(project_id, branch=branch)

    def drain(self, project_id: str, *, branch: str | None = None) -> DrainResult:
        return self.workflow.drain(project_id, branch=branch)

    def queue_and_run_first_loop(self, project_id: str, *, chapter_number: int = 1, branch: str = "main") -> list[TaskRecord]:
        tasks = self.queue_first_loop(project_id, chapter_number=chapter_number, branch=branch)
        self.drain(project_id, branch=branch)
        return tasks

    def cancel_task(self, task_id: str) -> TaskRecord:
        task = self.workflow.store.get_task(task_id)
        if task is None:
            raise ValueError("Task not found")
        if task.status == TaskStatus.running:
            raise RuntimeError("Running task cannot be cancelled safely")
        if task.status == TaskStatus.completed:
            raise RuntimeError("Completed task cannot be cancelled")
        if task.status == TaskStatus.cancelled:
            return task
        self.workflow.state_machine.transition(task, TaskStatus.cancelled, step="cancelled", progress=task.progress, error=task.error)
        self.workflow.store.save_task(task)
        self.workflow.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.cancelled,
                step="cancelled",
                message="Task cancelled",
                progress=task.progress,
            )
        )
        return task

    def retry_task(self, task_id: str) -> TaskRecord:
        task = self.workflow.store.get_task(task_id)
        if task is None:
            raise ValueError("Task not found")
        if task.status not in {TaskStatus.waiting_retry, TaskStatus.failed, TaskStatus.cancelled}:
            raise RuntimeError("Task is not in a retryable state")
        task.error = None
        self.workflow.state_machine.transition(task, TaskStatus.queued, step="queued.retry", progress=0.0)
        self.workflow.store.save_task(task)
        self.workflow.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.queued,
                step="queued.retry",
                message="Task re-queued",
                progress=0.0,
                payload={"retry_count": task.retry_count},
            )
        )
        return task

    def summarize_project(self, project_id: str) -> ProjectExecutionSummary:
        tasks = self.workflow.store.list_tasks(project_id)
        summary = TaskSummary()
        failure_summary = FailureSummary()
        latest_task_id: str | None = None
        latest_event_type: str | None = None
        latest_event_message: str | None = None
        latest_event_timestamp: str | None = None
        experiment_map: dict[tuple[str, str], QualityExperimentSummary] = {}
        review_key_by_asset_id: dict[str, tuple[str, str]] = {}
        review_keys_by_chapter_ref: dict[str, set[tuple[str, str]]] = {}
        review_keys_by_chapter_number: dict[int, set[tuple[str, str]]] = {}
        review_assets = self.workflow.store.list_assets(project_id)

        summary.total = len(tasks)
        for task in tasks:
            setattr(summary, task.status.value, getattr(summary, task.status.value) + 1)
            if task.status == TaskStatus.failed:
                failure_summary.failed_task_ids.append(task.task_id)
            if task.status == TaskStatus.waiting_retry:
                failure_summary.waiting_retry_task_ids.append(task.task_id)
            if task.error:
                failure_summary.last_error_by_task[task.task_id] = task.error
            events = self.workflow.store.list_events(task.task_id)
            if events:
                last_event = events[-1]
                if latest_event_timestamp is None or last_event.timestamp.isoformat() > latest_event_timestamp:
                    latest_event_timestamp = last_event.timestamp.isoformat()
                    latest_task_id = task.task_id
                    latest_event_type = last_event.event_type.value
                    latest_event_message = last_event.message

            values = task.effective_config_snapshot.values
            experiment = values.get("quality_experiment")
            variant = values.get("quality_variant")
            if not experiment or not variant:
                continue
            key = (str(experiment), str(variant))
            exp_summary = experiment_map.get(key)
            if exp_summary is None:
                exp_summary = QualityExperimentSummary(experiment=key[0], variant=key[1])
                experiment_map[key] = exp_summary
            if task.task_type == TaskType.chapter_review:
                exp_summary.review_count += 1
                for output_ref in task.output_refs:
                    review_key_by_asset_id[output_ref] = key
                chapter_ref = next(
                    (
                        ref_id
                        for ref_id in task.input_asset_refs
                        if (asset := self.workflow.store.get_asset_by_id(ref_id)) is not None and asset.asset_type == AssetType.chapter
                    ),
                    None,
                )
                if chapter_ref is not None:
                    review_keys_by_chapter_ref.setdefault(chapter_ref, set()).add(key)
                chapter_number = _coerce_chapter_number(task.payload.get("chapter_number"))
                if chapter_number is not None:
                    review_keys_by_chapter_number.setdefault(chapter_number, set()).add(key)
                usage = values.get("llm_usage", {})
                if isinstance(usage, dict):
                    exp_summary.input_tokens += _coerce_usage_count(usage.get("input_tokens", 0))
                    exp_summary.output_tokens += _coerce_usage_count(usage.get("output_tokens", 0))
                    exp_summary.total_tokens += _coerce_usage_count(usage.get("total_tokens", 0))

        for asset in review_assets:
            if asset.asset_type != AssetType.review_note or asset.is_deleted:
                continue
            structured = asset.structured_data
            experiment = structured.get("quality_experiment")
            variant = structured.get("quality_variant")
            key: tuple[str, str] | None = None
            if experiment and variant:
                key = (str(experiment), str(variant))
            else:
                key = review_key_by_asset_id.get(asset.asset_id)
                if key is None:
                    chapter_ref = structured.get("chapter_ref")
                    if isinstance(chapter_ref, str) and chapter_ref:
                        candidate_keys = review_keys_by_chapter_ref.get(chapter_ref, set())
                        if len(candidate_keys) == 1:
                            key = next(iter(candidate_keys))
                if key is None:
                    chapter_number = _coerce_chapter_number(structured.get("chapter_number"))
                    if chapter_number is not None:
                        candidate_keys = review_keys_by_chapter_number.get(chapter_number, set())
                        if len(candidate_keys) == 1:
                            key = next(iter(candidate_keys))
            if key is None:
                continue
            exp_summary = experiment_map.get(key)
            if exp_summary is None:
                exp_summary = QualityExperimentSummary(experiment=key[0], variant=key[1])
                experiment_map[key] = exp_summary
            approved = structured.get("approved")
            if approved is True:
                exp_summary.approved_count += 1
            elif approved is False:
                exp_summary.rewrite_count += 1
            issues = structured.get("issues", [])
            if isinstance(issues, list):
                exp_summary.issue_count += len(issues)

        return ProjectExecutionSummary(
            project_id=project_id,
            task_summary=summary,
            failure_summary=failure_summary,
            latest_task_id=latest_task_id,
            latest_event_type=latest_event_type,
            latest_event_message=latest_event_message,
            llm_usage=self.workflow.get_llm_usage(),
            quality_experiments=sorted(experiment_map.values(), key=lambda item: (item.experiment, item.variant)),
        )

    def queue_next_chapter(self, project_id: str, *, from_chapter: int | None = None, branch: str = "main") -> list[TaskRecord]:
        return self.workflow.queue_next_chapter(project_id, from_chapter=from_chapter, branch=branch)

    def queue_chapter_loop(self, project_id: str, *, target_chapter: int | None = None, branch: str = "main") -> list[TaskRecord]:
        return self.workflow.queue_chapter_loop(project_id, target_chapter=target_chapter, branch=branch)
