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

_BOOTSTRAP_ASSET_TYPES = (
    AssetType.world,
    AssetType.characters,
    AssetType.rules,
    AssetType.timeline,
    AssetType.style_profile,
    AssetType.foreshadowing,
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
        task_specs = [
            TaskType.brief_generation,
            TaskType.asset_bootstrap,
            TaskType.outline_generation,
            TaskType.chapter_generation,
            TaskType.chapter_validation,
            TaskType.chapter_audit,
            TaskType.chapter_revision,
            TaskType.chapter_reaudit,
            TaskType.final_save,
            TaskType.export_candidate,
        ]
        tasks: list[TaskRecord] = []
        parent_task_id: str | None = None
        for task_type in task_specs:
            task = self._accept_and_queue(
                TaskRecord(
                    project_id=project_id,
                    task_type=task_type,
                    branch=branch,
                    payload={"chapter_number": chapter_number, "branch": branch, "critical_path": True},
                    parent_task_id=parent_task_id,
                    input_asset_refs=[],
                )
            )
            tasks.append(task)
            parent_task_id = task.task_id
        return tasks

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
            output_assets = self._generate_outputs(task)
            for output_asset in output_assets:
                output_asset.branch = task.branch
                self.store.save_asset(output_asset)
                task.output_refs.append(output_asset.asset_id)
            self._update_project_state(task, output_assets)
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
        elif task.task_type == TaskType.asset_bootstrap:
            self._append_latest_ref(task, AssetType.brief)
        elif task.task_type == TaskType.outline_generation:
            self._append_latest_ref(task, AssetType.brief)
            self._append_latest_refs(task, _BOOTSTRAP_ASSET_TYPES)
        elif task.task_type == TaskType.chapter_generation:
            if task.payload.get("rewrite"):
                original_asset_id = task.payload.get("original_asset_id")
                if original_asset_id:
                    task.input_asset_refs.append(str(original_asset_id))
            self._append_latest_ref(task, AssetType.outline)
            self._append_latest_ref(task, AssetType.brief)
            self._append_latest_refs(task, _BOOTSTRAP_ASSET_TYPES)
            self._append_latest_ref(task, AssetType.continuity_note, required=False)
            self._append_latest_ref(task, AssetType.final_chapter, required=False)
        elif task.task_type == TaskType.chapter_validation:
            self._append_latest_ref(task, AssetType.chapter)
            self._append_context_refs(task)
        elif task.task_type == TaskType.chapter_audit:
            self._append_latest_ref(task, AssetType.chapter)
            self._append_latest_ref(task, AssetType.validation_report)
            self._append_context_refs(task)
        elif task.task_type == TaskType.chapter_revision:
            self._append_latest_ref(task, AssetType.chapter)
            self._append_latest_ref(task, AssetType.validation_report)
            self._append_latest_ref(task, AssetType.audit_report)
            self._append_context_refs(task)
        elif task.task_type == TaskType.chapter_reaudit:
            self._append_latest_ref(task, AssetType.chapter)
            self._append_latest_ref(task, AssetType.revision_delta)
            self._append_latest_ref(task, AssetType.validation_report)
            self._append_latest_ref(task, AssetType.audit_report)
            self._append_context_refs(task)
        elif task.task_type == TaskType.final_save:
            self._append_latest_ref(task, AssetType.chapter)
            self._append_latest_ref(task, AssetType.revision_delta)
            self._append_latest_ref(task, AssetType.audit_report)
        elif task.task_type == TaskType.export_candidate:
            self._append_all_refs(task, AssetType.final_chapter)
        elif task.task_type == TaskType.chapter_review:
            self._append_latest_ref(task, AssetType.chapter)
        elif task.task_type == TaskType.spot_fix:
            chapter_asset_id = task.payload.get("chapter_asset_id")
            if chapter_asset_id:
                task.input_asset_refs.append(chapter_asset_id)

    def _append_latest_ref(self, task: TaskRecord, asset_type: AssetType, *, required: bool = True) -> None:
        asset = self.store.get_latest_asset(task.project_id, asset_type, branch=task.branch)
        if asset is not None and not asset.is_deleted and asset.asset_id not in task.input_asset_refs:
            task.input_asset_refs.append(asset.asset_id)
        elif required:
            return

    def _append_latest_refs(self, task: TaskRecord, asset_types: tuple[AssetType, ...]) -> None:
        for asset_type in asset_types:
            self._append_latest_ref(task, asset_type)

    def _append_all_refs(self, task: TaskRecord, asset_type: AssetType) -> None:
        for asset in self.store.list_assets(task.project_id, asset_type, branch=task.branch):
            if not asset.is_deleted and asset.asset_id not in task.input_asset_refs:
                task.input_asset_refs.append(asset.asset_id)

    def _append_context_refs(self, task: TaskRecord) -> None:
        self._append_latest_ref(task, AssetType.brief)
        self._append_latest_ref(task, AssetType.outline)
        self._append_latest_refs(task, _BOOTSTRAP_ASSET_TYPES)
        self._append_latest_ref(task, AssetType.continuity_note, required=False)
        self._append_latest_ref(task, AssetType.final_chapter, required=False)

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

    def _generate_outputs(self, task: TaskRecord) -> list[Asset]:
        project = self.store.get_project(task.project_id)
        if project is None:
            raise ValueError("Project not found")
        chapter_number = int(task.payload.get("chapter_number", 1))
        if task.task_type == TaskType.brief_generation:
            return [self.generators.generate_brief(project)]
        if task.task_type == TaskType.asset_bootstrap:
            brief_asset = self._resolve_input(task, AssetType.brief)
            if brief_asset is None:
                raise ValueError("Brief asset is required before asset bootstrap")
            return self.generators.generate_asset_bootstrap(project, brief_asset)
        if task.task_type == TaskType.outline_generation:
            brief_asset = self._resolve_input(task, AssetType.brief)
            bootstrap_assets = self._resolve_bootstrap_inputs(task)
            if brief_asset is None:
                raise ValueError("Brief asset is required before outline generation")
            if len(bootstrap_assets) < len(_BOOTSTRAP_ASSET_TYPES):
                raise ValueError("Bootstrap assets are required before outline generation")
            return [self.generators.generate_outline(project, brief_asset, bootstrap_assets)]
        if task.task_type == TaskType.chapter_generation:
            outline_asset = self._resolve_input(task, AssetType.outline)
            brief_asset = self._resolve_input(task, AssetType.brief)
            bootstrap_assets = self._resolve_bootstrap_inputs(task)
            if outline_asset is None or brief_asset is None:
                raise ValueError("Brief and outline assets are required before chapter generation")
            if len(bootstrap_assets) < len(_BOOTSTRAP_ASSET_TYPES):
                raise ValueError("Bootstrap assets are required before chapter generation")
            rewrite_context = None
            if task.payload.get("rewrite"):
                original_asset_id = task.payload.get("original_asset_id")
                if original_asset_id:
                    original_asset = self._require_asset_for_task(task, str(original_asset_id), AssetType.chapter, label="Original chapter")
                    rewrite_context = {
                        "chapter_content": original_asset.content,
                        "review_issues": task.payload.get("review_issues", []),
                    }
            return [self.generators.generate_chapter(project, outline_asset, brief_asset, chapter_number, rewrite_context=rewrite_context, bootstrap_assets=bootstrap_assets)]
        if task.task_type == TaskType.chapter_validation:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            if chapter_asset is None:
                raise ValueError("Chapter draft is required before validation")
            context_assets = self._resolve_context_inputs(task)
            return [self.generators.generate_validation_report(project, chapter_asset, chapter_number, context_assets)]
        if task.task_type == TaskType.chapter_audit:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            validation_asset = self._resolve_input(task, AssetType.validation_report)
            if chapter_asset is None or validation_asset is None:
                raise ValueError("Chapter draft and validation report are required before audit")
            context_assets = self._resolve_context_inputs(task)
            return [self.generators.generate_audit_report(project, chapter_asset, validation_asset, chapter_number, context_assets)]
        if task.task_type == TaskType.chapter_revision:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            validation_asset = self._resolve_input(task, AssetType.validation_report)
            audit_asset = self._resolve_input(task, AssetType.audit_report)
            if chapter_asset is None or validation_asset is None or audit_asset is None:
                raise ValueError("Chapter draft, validation report, and audit report are required before revision")
            context_assets = self._resolve_context_inputs(task)
            return self.generators.generate_revision(project, chapter_asset, validation_asset, audit_asset, chapter_number, context_assets)
        if task.task_type == TaskType.chapter_reaudit:
            revised_asset = self._resolve_revision_chapter(task)
            revision_delta = self._resolve_input(task, AssetType.revision_delta)
            previous_validation = self._resolve_input(task, AssetType.validation_report)
            previous_audit = self._resolve_initial_audit(task)
            if revised_asset is None or revision_delta is None or previous_validation is None or previous_audit is None:
                raise ValueError("Revised chapter, revision delta, validation report, and audit report are required before re-audit")
            context_assets = self._resolve_context_inputs(task)
            return [self.generators.generate_reaudit_report(project, revised_asset, revision_delta, previous_validation, previous_audit, chapter_number, context_assets)]
        if task.task_type == TaskType.final_save:
            revised_asset = self._resolve_revision_chapter(task)
            reaudit_asset = self._resolve_reaudit(task)
            if revised_asset is None or reaudit_asset is None:
                raise ValueError("Revised chapter and re-audit report are required before final save")
            if not reaudit_asset.structured_data.get("passed"):
                raise ValueError("Re-audit did not pass; final save is blocked")
            return [self.generators.generate_final_chapter(project, revised_asset, reaudit_asset, chapter_number)]
        if task.task_type == TaskType.export_candidate:
            final_chapters = self._resolve_final_chapters(task)
            if not final_chapters:
                raise ValueError("Final chapter is required before export candidate")
            return [self.generators.generate_export_candidate(project, final_chapters, chapter_number)]
        if task.task_type == TaskType.chapter_review:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            if chapter_asset is None:
                raise ValueError("Chapter asset is required before review")
            return [self.generators.generate_review(project, chapter_asset, chapter_number, config=task.effective_config_snapshot)]
        if task.task_type == TaskType.spot_fix:
            chapter_asset = self._resolve_input(task, AssetType.chapter)
            if chapter_asset is None:
                raise ValueError("Chapter asset is required before spot fix")
            paragraph_indices = task.payload.get("paragraph_indices", [])
            fix_instruction = task.payload.get("fix_instruction", "")
            return [self.generators.generate_spot_fix(project, chapter_asset, paragraph_indices, fix_instruction)]
        raise ValueError(f"Unsupported task type: {task.task_type}")

    def _resolve_input(self, task: TaskRecord, asset_type: AssetType) -> Asset | None:
        """Resolve an input asset from the task's frozen input_asset_refs."""
        for ref_id in task.input_asset_refs:
            asset = self._validate_asset_for_task(task, self.store.get_asset_by_id(ref_id), asset_type)
            if asset is not None:
                return asset
        return None

    def _resolve_inputs(self, task: TaskRecord, asset_type: AssetType) -> list[Asset]:
        assets: list[Asset] = []
        for ref_id in task.input_asset_refs:
            asset = self._validate_asset_for_task(task, self.store.get_asset_by_id(ref_id), asset_type)
            if asset is not None:
                assets.append(asset)
        return assets

    def _resolve_bootstrap_inputs(self, task: TaskRecord) -> list[Asset]:
        return [asset for asset_type in _BOOTSTRAP_ASSET_TYPES if (asset := self._resolve_input(task, asset_type)) is not None]

    def _resolve_context_inputs(self, task: TaskRecord) -> list[Asset]:
        context_types = (AssetType.brief, AssetType.outline, *_BOOTSTRAP_ASSET_TYPES, AssetType.continuity_note, AssetType.final_chapter)
        assets: list[Asset] = []
        seen: set[str] = set()
        for asset_type in context_types:
            for asset in self._resolve_inputs(task, asset_type):
                if asset.asset_id not in seen:
                    assets.append(asset)
                    seen.add(asset.asset_id)
        return assets

    def _resolve_revision_chapter(self, task: TaskRecord) -> Asset | None:
        delta = self._resolve_input(task, AssetType.revision_delta)
        if delta is not None:
            revised_ref = delta.structured_data.get("revised_ref")
            if isinstance(revised_ref, str) and revised_ref:
                revised = self._validate_asset_for_task(task, self.store.get_asset_by_id(revised_ref), AssetType.chapter)
                if revised is not None:
                    return revised
        chapters = [asset for asset in self._resolve_inputs(task, AssetType.chapter) if asset.structured_data.get("is_revision") is True]
        return chapters[-1] if chapters else self._resolve_input(task, AssetType.chapter)

    def _resolve_initial_audit(self, task: TaskRecord) -> Asset | None:
        audits = [asset for asset in self._resolve_inputs(task, AssetType.audit_report) if asset.structured_data.get("kind") != "re_audit"]
        return audits[-1] if audits else None

    def _resolve_reaudit(self, task: TaskRecord) -> Asset | None:
        audits = [asset for asset in self._resolve_inputs(task, AssetType.audit_report) if asset.structured_data.get("kind") == "re_audit"]
        return audits[-1] if audits else None

    def _resolve_final_chapters(self, task: TaskRecord) -> list[Asset]:
        return self._resolve_inputs(task, AssetType.final_chapter)

    def _update_project_state(self, task: TaskRecord, output_assets: list[Asset]) -> None:
        project = self.store.get_project(task.project_id)
        if project is None:
            raise ValueError("Project not found")
        output_asset = output_assets[0] if output_assets else None
        if task.task_type == TaskType.brief_generation and output_asset is not None:
            project.title = output_asset.structured_data.get("title", project.title)
            project.genre = output_asset.structured_data.get("genre", project.genre)
            project.brief = output_asset.structured_data.get("summary", project.brief)
            project.target_length = output_asset.structured_data.get("target_length", project.target_length)
            project.current_phase = ProjectPhase.briefing
        elif task.task_type == TaskType.asset_bootstrap:
            project.current_phase = ProjectPhase.outlining
        elif task.task_type == TaskType.outline_generation:
            project.current_phase = ProjectPhase.outlining
            project.latest_outline_version += 1
        elif task.task_type == TaskType.chapter_generation:
            project.current_phase = ProjectPhase.drafting
        elif task.task_type in {TaskType.chapter_validation, TaskType.chapter_audit, TaskType.chapter_revision, TaskType.chapter_reaudit}:
            project.current_phase = ProjectPhase.review
        elif task.task_type == TaskType.final_save:
            chapter_number = int(task.payload.get("chapter_number", 1))
            project.current_phase = ProjectPhase.publishing
            project.latest_chapter_cursor = max(project.latest_chapter_cursor, chapter_number)
        elif task.task_type == TaskType.export_candidate:
            project.current_phase = ProjectPhase.publishing
        elif task.task_type == TaskType.chapter_review and output_asset is not None:
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
        final_chapters = self.store.list_assets(project_id, AssetType.final_chapter, branch=branch)
        chapter_assets = final_chapters or self.store.list_assets(project_id, AssetType.chapter, branch=branch)
        for asset in chapter_assets:
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

        brief_asset = self.store.get_latest_asset(project_id, AssetType.brief, branch=branch)
        if brief_asset is None:
            raise ValueError("Cannot queue next chapter: no brief exists")
        if outline_asset is None:
            raise ValueError("Cannot queue next chapter: no outline exists")
        bootstrap_assets = [self.store.get_latest_asset(project_id, asset_type, branch=branch) for asset_type in _BOOTSTRAP_ASSET_TYPES]
        if any(asset is None for asset in bootstrap_assets):
            raise ValueError("Cannot queue next chapter: bootstrap assets are incomplete")
        input_refs = [outline_asset.asset_id, brief_asset.asset_id, *(asset.asset_id for asset in bootstrap_assets if asset is not None)]
        previous_final = self.store.get_latest_asset(project_id, AssetType.final_chapter, branch=branch)
        if previous_final is not None:
            input_refs.append(previous_final.asset_id)
        task_specs = [
            TaskType.chapter_generation,
            TaskType.chapter_validation,
            TaskType.chapter_audit,
            TaskType.chapter_revision,
            TaskType.chapter_reaudit,
            TaskType.final_save,
            TaskType.export_candidate,
        ]
        tasks: list[TaskRecord] = []
        parent_task_id: str | None = None
        for index, task_type in enumerate(task_specs):
            task = self._accept_and_queue(
                TaskRecord(
                    project_id=project_id,
                    task_type=task_type,
                    branch=branch,
                    payload={"chapter_number": start, "branch": branch, "critical_path": True},
                    parent_task_id=parent_task_id,
                    input_asset_refs=input_refs[:] if index == 0 else [],
                )
            )
            tasks.append(task)
            parent_task_id = task.task_id
        return tasks

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

