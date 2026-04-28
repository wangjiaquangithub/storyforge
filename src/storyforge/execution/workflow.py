from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from storyforge.config import LlmConfig
from storyforge.domain.models import (
    Asset,
    AssetType,
    ConfigSnapshot,
    EventRecord,
    EventType,
    Project,
    ProjectPhase,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from storyforge.execution.generators import StoryForgeGenerators
from storyforge.execution.state_machine import TaskStateMachine
from storyforge.execution.store import StoryForgeStore
from storyforge.llm import LlmUsage
from storyforge.realtime import EventBus


_SYSTEM_DEFAULT_CONFIG = ConfigSnapshot(
    provider="openai",
    model="gpt-4o",
    review_policy="default",
    retry_limit=2,
    chapter_length_target=2000,
)


@dataclass
class DrainResult:
    processed_task_ids: list[str]

    @property
    def processed_count(self) -> int:
        return len(self.processed_task_ids)


class ClosedLoopService:
    def __init__(self, store: StoryForgeStore, state_machine: TaskStateMachine, event_bus: EventBus | None = None, llm_config: LlmConfig | None = None) -> None:
        self.store = store
        self.state_machine = state_machine
        self.event_bus = event_bus
        self.generators = StoryForgeGenerators(store, llm_config=llm_config)
        self._project_locks: dict[str, threading.Lock] = {}
        self._project_locks_guard = threading.Lock()

    def queue_first_loop(self, project_id: str, *, chapter_number: int = 1, branch: str = "main") -> list[TaskRecord]:
        project = self.store.get_project(project_id)
        if project is None:
            raise ValueError("Project not found")
        brief_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.brief_generation,
                branch=branch,
                payload={"chapter_number": chapter_number, "branch": branch},
            )
        )
        outline_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.outline_generation,
                branch=branch,
                payload={"chapter_number": chapter_number, "branch": branch},
                parent_task_id=brief_task.task_id,
                input_asset_refs=[],
            )
        )
        chapter_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.chapter_generation,
                branch=branch,
                payload={"chapter_number": chapter_number, "branch": branch},
                parent_task_id=outline_task.task_id,
                input_asset_refs=[],
            )
        )
        review_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.chapter_review,
                branch=branch,
                payload={"chapter_number": chapter_number, "branch": branch},
                parent_task_id=chapter_task.task_id,
                input_asset_refs=[],
            )
        )
        return [brief_task, outline_task, chapter_task, review_task]

    def record_event(self, event: EventRecord) -> EventRecord:
        recorded = self.store.append_event(event)
        if self.event_bus is not None:
            self.event_bus.publish(recorded)
        return recorded

    def process_next(self, project_id: str, *, branch: str | None = None) -> TaskRecord | None:
        with self._project_lock(project_id):
            for task in self.store.list_tasks(project_id, branch=branch):
                if self._is_runnable(task):
                    return self._execute(task)
        return None

    def drain(self, project_id: str, *, branch: str | None = None) -> DrainResult:
        with self._project_lock(project_id):
            processed: list[str] = []
            while True:
                task = self._process_next_unlocked(project_id, branch=branch)
                if task is None:
                    return DrainResult(processed_task_ids=processed)
                processed.append(task.task_id)

    def _process_next_unlocked(self, project_id: str, *, branch: str | None = None) -> TaskRecord | None:
        for task in self.store.list_tasks(project_id, branch=branch):
            if self._is_runnable(task):
                return self._execute(task)
        return None

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._project_locks_guard:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[project_id] = lock
            return lock

    def _is_runnable(self, task: TaskRecord) -> bool:
        if task.status == TaskStatus.waiting_retry:
            if not self._retry_delay_elapsed(task):
                return False
            self._requeue_retry(task)
        if task.status != TaskStatus.queued:
            return False
        if task.parent_task_id is None:
            return True
        parent = self.store.get_task(task.parent_task_id)
        if parent is None:
            return False
        if parent.status == TaskStatus.completed:
            return True
        if parent.status in {TaskStatus.failed, TaskStatus.cancelled}:
            self._cancel_blocked_child(task, parent)
        return False

    def _retry_delay_elapsed(self, task: TaskRecord) -> bool:
        delay_seconds = task.effective_config_snapshot.values.get("retry_delay_seconds", 0)
        try:
            delay_seconds = float(delay_seconds)
        except (TypeError, ValueError):
            delay_seconds = 0
        retry_ready_at = task.effective_config_snapshot.values.get("retry_ready_at")
        if isinstance(retry_ready_at, str):
            try:
                retry_ready_at = datetime.fromisoformat(retry_ready_at)
            except ValueError:
                return False
        if not isinstance(retry_ready_at, datetime):
            return False
        return utc_now() >= retry_ready_at

    def _set_retry_ready_at(self, task: TaskRecord) -> datetime:
        delay_seconds = task.effective_config_snapshot.values.get("retry_delay_seconds", 0)
        try:
            delay_seconds = float(delay_seconds)
        except (TypeError, ValueError):
            delay_seconds = 0
        retry_ready_at = utc_now() + timedelta(seconds=max(delay_seconds, 0))
        task.effective_config_snapshot.values["retry_ready_at"] = retry_ready_at.isoformat()
        return retry_ready_at

    def _cancel_blocked_child(self, task: TaskRecord, parent: TaskRecord) -> None:
        message = f"Parent task {parent.task_id} is {parent.status.value}; child task cannot run"
        self.state_machine.transition(
            task,
            TaskStatus.cancelled,
            step=f"{task.task_type.value}.parent_{parent.status.value}",
            progress=task.progress,
            error=message,
        )
        self.store.save_task(task)
        self.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.cancelled,
                step=f"{task.task_type.value}.parent_{parent.status.value}",
                message=message,
                progress=task.progress,
                payload={"parent_task_id": parent.task_id, "parent_status": parent.status.value},
            )
        )

    def _requeue_retry(self, task: TaskRecord) -> None:
        task.error = None
        task.effective_config_snapshot.values.pop("retry_ready_at", None)
        self.state_machine.transition(task, TaskStatus.queued, step="queued.retry.auto", progress=0.0)
        self.store.save_task(task)
        self.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.queued,
                step="queued.retry.auto",
                message="Task automatically re-queued for retry",
                progress=0.0,
                payload={"retry_count": task.retry_count},
            )
        )

    def _accept_and_queue(self, task: TaskRecord) -> TaskRecord:
        task.effective_config_snapshot = self._build_effective_config(task)
        self.store.create_task(task)
        self.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.accepted,
                step="accepted",
                message=f"Task accepted: {task.task_type}",
            )
        )
        self.state_machine.transition(task, TaskStatus.queued, step="queued", progress=0.0)
        self.store.save_task(task)
        self.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.queued,
                step="queued",
                message="Task queued",
            )
        )
        return task

    def _execute(self, task: TaskRecord) -> TaskRecord:
        self.state_machine.transition(task, TaskStatus.running, step=task.task_type.value, progress=0.1)
        self._freeze_input_refs(task)
        self.store.save_task(task)
        self.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.started,
                step=task.task_type.value,
                message=f"Running {task.task_type.value}",
                progress=0.1,
            )
        )
        try:
            self._validate_input_refs(task)
            output_asset = self._generate_output(task)
            output_asset.branch = task.branch
            self.store.save_asset(output_asset)
            self._update_project_state(task, output_asset)
            task.output_refs.append(output_asset.asset_id)
            self.state_machine.transition(task, TaskStatus.completed, step=f"{task.task_type.value}.completed", progress=1.0)
            self.store.save_task(task)
            self.record_event(
                EventRecord(
                    project_id=task.project_id,
                    task_id=task.task_id,
                    event_type=EventType.completed,
                    step=f"{task.task_type.value}.completed",
                    message="Task completed",
                    progress=1.0,
                    payload={"output_refs": task.output_refs},
                )
            )
        except Exception as exc:
            retry_limit = task.effective_config_snapshot.retry_limit
            if task.retry_count < retry_limit:
                task.retry_count += 1
                retry_ready_at = self._set_retry_ready_at(task)
                self.state_machine.transition(
                    task,
                    TaskStatus.waiting_retry,
                    step=f"{task.task_type.value}.waiting_retry",
                    progress=0.0,
                    error=str(exc),
                )
                self.store.save_task(task)
                self.record_event(
                    EventRecord(
                        project_id=task.project_id,
                        task_id=task.task_id,
                        event_type=EventType.waiting_retry,
                        step=f"{task.task_type.value}.waiting_retry",
                        message=str(exc),
                        progress=0.0,
                        payload={
                            "error": str(exc),
                            "retry_count": task.retry_count,
                            "retry_limit": retry_limit,
                            "retry_ready_at": retry_ready_at.isoformat(),
                        },
                    )
                )
            else:
                self.state_machine.transition(task, TaskStatus.failed, step=f"{task.task_type.value}.failed", progress=1.0, error=str(exc))
                self.store.save_task(task)
                self.record_event(
                    EventRecord(
                        project_id=task.project_id,
                        task_id=task.task_id,
                        event_type=EventType.failed,
                        step=f"{task.task_type.value}.failed",
                        message=str(exc),
                        progress=1.0,
                        payload={"error": str(exc)},
                    )
                )
        return task

    def _build_effective_config(self, task: TaskRecord) -> ConfigSnapshot:
        """Merge system defaults, project overrides, and task-level config into a frozen snapshot."""
        project = self.store.get_project(task.project_id)
        base = ConfigSnapshot(**_SYSTEM_DEFAULT_CONFIG.model_dump())
        if project is not None:
            if project.target_length:
                base.chapter_length_target = project.target_length
        incoming = task.effective_config_snapshot
        if incoming.provider:
            base.provider = incoming.provider
        if incoming.model:
            base.model = incoming.model
        if incoming.review_policy:
            base.review_policy = incoming.review_policy
        if incoming.retry_limit >= 0:
            base.retry_limit = incoming.retry_limit
        if incoming.chapter_length_target > 0:
            base.chapter_length_target = incoming.chapter_length_target
        for key, value in incoming.values.items():
            base.values[key] = value
        return base

    def _freeze_input_refs(self, task: TaskRecord) -> None:
        """Resolve and freeze input_asset_refs before execution."""
        if task.input_asset_refs:
            return
        project_id = task.project_id
        if task.task_type == TaskType.brief_generation:
            pass
        elif task.task_type == TaskType.outline_generation:
            brief = self.store.get_latest_asset(project_id, AssetType.brief, branch=task.branch)
            if brief:
                task.input_asset_refs.append(brief.asset_id)
        elif task.task_type == TaskType.chapter_generation:
            if task.payload.get("rewrite"):
                original_asset_id = task.payload.get("original_asset_id")
                if original_asset_id:
                    task.input_asset_refs.append(str(original_asset_id))
            outline = self.store.get_latest_asset(project_id, AssetType.outline, branch=task.branch)
            brief = self.store.get_latest_asset(project_id, AssetType.brief, branch=task.branch)
            if outline:
                task.input_asset_refs.append(outline.asset_id)
            if brief:
                task.input_asset_refs.append(brief.asset_id)
        elif task.task_type == TaskType.chapter_review:
            chapter = self.store.get_latest_asset(project_id, AssetType.chapter, branch=task.branch)
            if chapter:
                task.input_asset_refs.append(chapter.asset_id)
        elif task.task_type == TaskType.spot_fix:
            chapter_asset_id = task.payload.get("chapter_asset_id")
            if chapter_asset_id:
                task.input_asset_refs.append(chapter_asset_id)

    def _validate_asset_for_task(self, task: TaskRecord, asset: Asset | None, asset_type: AssetType | None = None) -> Asset | None:
        if asset is None:
            return None
        if asset.project_id != task.project_id or asset.branch != task.branch or asset.is_deleted:
            return None
        if asset_type is not None and asset.asset_type != asset_type:
            return None
        return asset

    def _require_asset_for_task(self, task: TaskRecord, asset_id: str, asset_type: AssetType | None = None, *, label: str = "Asset") -> Asset:
        asset = self._validate_asset_for_task(task, self.store.get_asset_by_id(asset_id), asset_type)
        if asset is None:
            raise ValueError(f"{label} not found")
        return asset

    def _validate_input_refs(self, task: TaskRecord) -> None:
        for ref_id in task.input_asset_refs:
            self._require_asset_for_task(task, ref_id, label="Input asset")
        if task.task_type == TaskType.spot_fix:
            chapter_asset_id = task.payload.get("chapter_asset_id")
            if chapter_asset_id:
                self._require_asset_for_task(task, str(chapter_asset_id), AssetType.chapter, label="Chapter asset")
        if task.task_type == TaskType.chapter_generation and task.payload.get("rewrite"):
            original_asset_id = task.payload.get("original_asset_id")
            if original_asset_id:
                self._require_asset_for_task(task, str(original_asset_id), AssetType.chapter, label="Original chapter")

    def _generate_output(self, task: TaskRecord) -> Asset:
        project = self.store.get_project(task.project_id)
        if project is None:
            raise ValueError("Project not found")
        if task.task_type == TaskType.brief_generation:
            return self.generators.generate_brief(project)
        if task.task_type == TaskType.outline_generation:
            brief_asset = self._resolve_input(task, AssetType.brief)
            if brief_asset is None:
                raise ValueError("Brief asset is required before outline generation")
            return self.generators.generate_outline(project, brief_asset)
        if task.task_type == TaskType.chapter_generation:
            outline_asset = self._resolve_input(task, AssetType.outline)
            brief_asset = self._resolve_input(task, AssetType.brief)
            if outline_asset is None or brief_asset is None:
                raise ValueError("Brief and outline assets are required before chapter generation")
            chapter_number = int(task.payload.get("chapter_number", 1))
            rewrite_context = None
            if task.payload.get("rewrite"):
                # Gather rewrite context from review issues and original chapter
                original_asset_id = task.payload.get("original_asset_id")
                if original_asset_id:
                    original_asset = self._require_asset_for_task(task, str(original_asset_id), AssetType.chapter, label="Original chapter")
                    rewrite_context = {
                        "chapter_content": original_asset.content,
                        "review_issues": task.payload.get("review_issues", []),
                    }
            return self.generators.generate_chapter(
                project, outline_asset, brief_asset, chapter_number,
                rewrite_context=rewrite_context,
            )
        if task.task_type == TaskType.chapter_review:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            if chapter_asset is None:
                raise ValueError("Chapter asset is required before review")
            chapter_number = int(task.payload.get("chapter_number", 1))
            return self.generators.generate_review(
                project,
                chapter_asset,
                chapter_number,
                config=task.effective_config_snapshot,
            )
        if task.task_type == TaskType.spot_fix:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            if chapter_asset is None:
                raise ValueError("Chapter asset is required before spot fix")
            paragraph_indices = task.payload.get("paragraph_indices", [])
            fix_instruction = task.payload.get("fix_instruction", "")
            return self.generators.generate_spot_fix(project, chapter_asset, paragraph_indices, fix_instruction)
        raise ValueError(f"Unsupported task type: {task.task_type}")

    def _resolve_input(self, task: TaskRecord, asset_type: AssetType) -> Asset | None:
        """Resolve an input asset from the task's frozen input_asset_refs."""
        for ref_id in task.input_asset_refs:
            asset = self._validate_asset_for_task(task, self.store.get_asset_by_id(ref_id), asset_type)
            if asset is not None:
                return asset
        return None

    def _update_project_state(self, task: TaskRecord, output_asset: Asset) -> None:
        project = self.store.get_project(task.project_id)
        if project is None:
            raise ValueError("Project not found")
        if task.task_type == TaskType.brief_generation:
            project.title = output_asset.structured_data.get("title", project.title)
            project.genre = output_asset.structured_data.get("genre", project.genre)
            project.brief = output_asset.structured_data.get("summary", project.brief)
            project.target_length = output_asset.structured_data.get("target_length", project.target_length)
            project.current_phase = ProjectPhase.briefing
        elif task.task_type == TaskType.outline_generation:
            project.current_phase = ProjectPhase.outlining
            project.latest_outline_version += 1
        elif task.task_type == TaskType.chapter_generation:
            chapter_number = int(task.payload.get("chapter_number", 1))
            project.current_phase = ProjectPhase.drafting
            project.latest_chapter_cursor = max(project.latest_chapter_cursor, chapter_number)
        elif task.task_type == TaskType.chapter_review:
            project.current_phase = ProjectPhase.review
            self._handle_review_continuation(task, output_asset)
        project.updated_at = utc_now()
        self.store.save_project(project)

    def _handle_review_continuation(self, review_task: TaskRecord, review_asset: Asset) -> None:
        """Queue a rewrite task when review is rejected. Does NOT auto-queue next chapter."""
        approved = review_asset.structured_data.get("approved", True)

        if not approved:
            # Find the chapter asset from input refs
            chapter_asset_id = None
            for ref_id in review_task.input_asset_refs:
                asset = self._validate_asset_for_task(review_task, self.store.get_asset_by_id(ref_id), AssetType.chapter)
                if asset is not None:
                    chapter_asset_id = ref_id
                    break

            issues = review_asset.structured_data.get("issues", [])
            if chapter_asset_id is None:
                raise ValueError("Cannot queue rewrite: reviewed chapter asset is missing")
            rewrite_input_refs = list(review_task.input_asset_refs)
            for required_type in (AssetType.outline, AssetType.brief):
                asset = self._validate_asset_for_task(
                    review_task,
                    self.store.get_latest_asset(review_task.project_id, required_type, branch=review_task.branch),
                    required_type,
                )
                if asset is not None and asset.asset_id not in rewrite_input_refs:
                    rewrite_input_refs.append(asset.asset_id)

            rewrite_task = TaskRecord(
                project_id=review_task.project_id,
                task_type=TaskType.chapter_generation,
                branch=review_task.branch,
                payload={"chapter_number": review_task.payload.get("chapter_number", 1), "branch": review_task.branch, "rewrite": True, "review_issues": issues, "original_asset_id": chapter_asset_id},
                parent_task_id=review_task.task_id,
                input_asset_refs=rewrite_input_refs,
            )
            rewrite_task.effective_config_snapshot = self._build_effective_config(rewrite_task)
            self.store.create_task(rewrite_task)
            self.record_event(
                EventRecord(
                    project_id=rewrite_task.project_id,
                    task_id=rewrite_task.task_id,
                    event_type=EventType.accepted,
                    step="accepted",
                    message=f"Task accepted: {rewrite_task.task_type.value} (rewrite)",
                )
            )
            self.state_machine.transition(rewrite_task, TaskStatus.queued, step="queued", progress=0.0)
            self.store.save_task(rewrite_task)
            self.record_event(
                EventRecord(
                    project_id=rewrite_task.project_id,
                    task_id=rewrite_task.task_id,
                    event_type=EventType.queued,
                    step="queued",
                    message="Rewrite task queued",
                )
            )

            follow_up_review_task = TaskRecord(
                project_id=review_task.project_id,
                task_type=TaskType.chapter_review,
                branch=review_task.branch,
                payload={"chapter_number": review_task.payload.get("chapter_number", 1), "branch": review_task.branch, "rewrite_review": True},
                parent_task_id=rewrite_task.task_id,
                input_asset_refs=[],
            )
            self._accept_and_queue(follow_up_review_task)

    def _latest_chapter_number(self, project_id: str, branch: str) -> int:
        latest = 0
        for asset in self.store.list_assets(project_id, AssetType.chapter, branch=branch):
            if asset.is_deleted or asset.structured_data.get("is_spot_fix") is True:
                continue
            value = asset.structured_data.get("chapter_number")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                continue
            latest = max(latest, value)
        return latest

    def queue_next_chapter(self, project_id: str, *, from_chapter: int | None = None, branch: str = "main") -> list[TaskRecord]:
        """Queue the next chapter pipeline after review passes.

        If *from_chapter* is provided, starts from ``from_chapter + 1``.
        Otherwise uses the latest chapter in the selected branch.

        When the next chapter exceeds the current outline, the outline is
        expanded first.
        """
        project = self.store.get_project(project_id)
        if project is None:
            raise ValueError("Project not found")
        if not project.auto_mode:
            raise ValueError("Project is in manual mode — queue tasks explicitly by the user")

        start = from_chapter + 1 if from_chapter is not None else self._latest_chapter_number(project_id, branch) + 1

        # Check if the outline covers this chapter
        outline_asset = self.store.get_latest_asset(project_id, AssetType.outline, branch=branch)
        max_outline_chapter = 0
        if outline_asset is not None:
            chapters = outline_asset.structured_data.get("chapters", [])
            if chapters:
                max_outline_chapter = max(c.get("chapter_number", 0) for c in chapters)

        # Expand outline if needed
        if start > max_outline_chapter:
            if outline_asset is None:
                raise ValueError("Cannot queue next chapter: no outline exists")
            expanded = self.generators.expand_outline(project, outline_asset, from_chapter=start)
            expanded.branch = branch
            self.store.save_asset(expanded)
            project.latest_outline_version += 1
            project.updated_at = utc_now()
            self.store.save_project(project)
            outline_asset = expanded

        # Queue chapter + review against existing project brief/outline state.
        brief_asset = self.store.get_latest_asset(project_id, AssetType.brief, branch=branch)
        if brief_asset is None:
            raise ValueError("Cannot queue next chapter: no brief exists")
        if outline_asset is None:
            raise ValueError("Cannot queue next chapter: no outline exists")
        chapter_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.chapter_generation,
                branch=branch,
                payload={"chapter_number": start, "branch": branch},
                input_asset_refs=[outline_asset.asset_id, brief_asset.asset_id],
            )
        )
        review_task = self._accept_and_queue(
            TaskRecord(
                project_id=project_id,
                task_type=TaskType.chapter_review,
                branch=branch,
                payload={"chapter_number": start, "branch": branch},
                parent_task_id=chapter_task.task_id,
                input_asset_refs=[],
            )
        )
        return [chapter_task, review_task]

    def queue_chapter_loop(self, project_id: str, *, target_chapter: int | None = None, branch: str = "main") -> list[TaskRecord]:
        """Queue chapters from ``latest_chapter_cursor + 1`` up to *target_chapter*.

        If *target_chapter* is None, uses the last chapter in the outline.
        """
        project = self.store.get_project(project_id)
        if project is None:
            raise ValueError("Project not found")

        if target_chapter is None:
            outline_asset = self.store.get_latest_asset(project_id, AssetType.outline, branch=branch)
            if outline_asset is None:
                raise ValueError("No outline exists to determine chapter range")
            chapters = outline_asset.structured_data.get("chapters", [])
            if not chapters:
                raise ValueError("Outline has no chapters")
            target_chapter = max(c.get("chapter_number", 0) for c in chapters)

        latest_chapter = self._latest_chapter_number(project_id, branch)
        if target_chapter <= latest_chapter:
            raise ValueError(
                f"No more chapters to queue: target={target_chapter}, cursor={latest_chapter}"
            )

        all_tasks: list[TaskRecord] = []
        for ch in range(latest_chapter + 1, target_chapter + 1):
            tasks = self.queue_next_chapter(project_id, from_chapter=ch - 1, branch=branch)
            all_tasks.extend(tasks)
        return all_tasks

    def get_llm_usage(self) -> LlmUsage:
        """Return accumulated LLM token usage for this service instance."""
        return self.generators.get_total_usage()

