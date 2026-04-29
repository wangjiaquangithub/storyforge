"""Tests for multi-chapter pipeline features."""

from storyforge.domain.models import (
    Asset,
    AssetType,
    Project,
    TaskRecord,
    TaskStatus,
    TaskType,
    ProjectPhase,
)
from storyforge.execution.store import InMemoryStoryForgeStore
from storyforge.execution.state_machine import TaskStateMachine
from storyforge.execution.workflow import ClosedLoopService


def _build_store_and_service():
    store = InMemoryStoryForgeStore()
    state_machine = TaskStateMachine()
    service = ClosedLoopService(store, state_machine)
    return store, state_machine, service


def _create_project(store, idea="test idea"):
    project = Project(idea=idea)
    return store.create_project(project)


BOOTSTRAP_ASSET_TYPES = (
    AssetType.world,
    AssetType.characters,
    AssetType.rules,
    AssetType.timeline,
    AssetType.style_profile,
    AssetType.foreshadowing,
)

NEXT_CHAPTER_TASK_TYPES = [
    TaskType.chapter_generation,
    TaskType.chapter_validation,
    TaskType.chapter_audit,
    TaskType.chapter_revision,
    TaskType.chapter_reaudit,
    TaskType.final_save,
    TaskType.export_candidate,
]


def _seed_completed_chapter(store, service, project, chapter_number=1):
    """Seed a completed critical path through a finalized chapter."""
    brief = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.brief_generation,
        payload={"chapter_number": chapter_number},
    ))
    brief.effective_config_snapshot.retry_limit = 0
    store.save_task(brief)
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, content="brief"))
    for asset_type in BOOTSTRAP_ASSET_TYPES:
        store.save_asset(Asset(project_id=project.project_id, asset_type=asset_type, content=f"{asset_type.value} context"))

    outline = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.outline_generation,
        payload={"chapter_number": chapter_number},
        parent_task_id=brief.task_id,
    ))
    outline.effective_config_snapshot.retry_limit = 0
    store.save_task(outline)
    chapters = [
        {"chapter_number": i, "arc_number": 1, "title": f"Ch{i}", "summary": f"Beat {i}"}
        for i in range(1, 4)
    ]
    store.save_asset(Asset(
        project_id=project.project_id,
        asset_type=AssetType.outline,
        content="outline",
        structured_data={
            "chapters": chapters,
            "arcs": [
                {"arc_number": 1, "title": "Arc 1", "chapters": chapters},
            ],
        },
    ))

    chapter = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.chapter_generation,
        payload={"chapter_number": chapter_number},
        parent_task_id=outline.task_id,
    ))
    chapter.effective_config_snapshot.retry_limit = 0
    store.save_task(chapter)
    store.save_asset(Asset(
        project_id=project.project_id,
        asset_type=AssetType.final_chapter,
        content="chapter text",
        structured_data={"chapter_number": chapter_number, "title": f"Ch{chapter_number}", "finalized": True},
    ))

    final_save = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.final_save,
        payload={"chapter_number": chapter_number},
        parent_task_id=chapter.task_id,
    ))
    final_save.effective_config_snapshot.retry_limit = 0
    store.save_task(final_save)

    for task in [brief, outline, chapter, final_save]:
        service.state_machine.transition(task, TaskStatus.queued, step="queued", progress=0.0)
        service.state_machine.transition(task, TaskStatus.running, step="running", progress=0.1)
        service.state_machine.transition(task, TaskStatus.completed, step="completed", progress=1.0)
        store.save_task(task)

    project.latest_chapter_cursor = chapter_number
    project.current_phase = ProjectPhase.publishing
    store.save_project(project)


def test_queue_next_chapter_queues_chapter_and_review_from_existing_state():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=1)

    brief_asset = store.get_latest_asset(project.project_id, AssetType.brief)
    outline_asset = store.get_latest_asset(project.project_id, AssetType.outline)
    bootstrap_assets = [store.get_latest_asset(project.project_id, asset_type) for asset_type in BOOTSTRAP_ASSET_TYPES]
    final_chapter = store.get_latest_asset(project.project_id, AssetType.final_chapter)
    tasks = service.queue_next_chapter(project.project_id)

    assert [task.task_type for task in tasks] == NEXT_CHAPTER_TASK_TYPES
    assert [task.payload["chapter_number"] for task in tasks] == [2, 2, 2, 2, 2, 2, 2]
    assert tasks[0].parent_task_id is None
    assert tasks[0].input_asset_refs == [outline_asset.asset_id, brief_asset.asset_id, *(asset.asset_id for asset in bootstrap_assets), final_chapter.asset_id]
    for parent, child in zip(tasks, tasks[1:]):
        assert child.parent_task_id == parent.task_id


def test_queue_next_chapter_expands_outline_when_exhausted():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=3)

    # Outline only has chapters 1-3, cursor is at 3
    tasks = service.queue_next_chapter(project.project_id)

    assert [task.task_type for task in tasks] == NEXT_CHAPTER_TASK_TYPES
    assert tasks[0].payload["chapter_number"] == 4

    # Outline should now have more chapters
    outline = store.get_latest_asset(project.project_id, AssetType.outline)
    chapters = outline.structured_data.get("chapters", [])
    assert len(chapters) > 3


def test_expand_outline_does_not_mutate_previous_outline_version():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=3)

    previous_outline = store.get_latest_asset(project.project_id, AssetType.outline)
    previous_chapters = list(previous_outline.structured_data.get("chapters", []))
    previous_arcs = previous_outline.structured_data.get("arcs", [])
    previous_arc_chapters = [list(arc.get("chapters", [])) for arc in previous_arcs]

    service.queue_next_chapter(project.project_id)

    latest_outline = store.get_latest_asset(project.project_id, AssetType.outline)
    assert len(latest_outline.structured_data.get("chapters", [])) > len(previous_chapters)

    outline_versions = store.list_asset_versions(project.project_id, AssetType.outline)
    assert len(outline_versions) == 2
    assert outline_versions[0].asset_id == previous_outline.asset_id
    assert outline_versions[0].structured_data.get("chapters", []) == previous_chapters
    assert [arc.get("chapters", []) for arc in outline_versions[0].structured_data.get("arcs", [])] == previous_arc_chapters


def test_queue_chapter_loop_queues_multiple_chapters():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=1)

    tasks = service.queue_chapter_loop(project.project_id, target_chapter=3)

    # 2 chapters (2, 3) * 7 critical-path tasks each = 14 tasks
    assert len(tasks) == 14
    assert [task.task_type for task in tasks[:7]] == NEXT_CHAPTER_TASK_TYPES
    assert [task.task_type for task in tasks[7:]] == NEXT_CHAPTER_TASK_TYPES


def test_queue_chapter_loop_raises_when_no_more_chapters():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=3)

    # Cursor at 3, outline ends at 3
    try:
        service.queue_chapter_loop(project.project_id, target_chapter=3)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "No more chapters to queue" in str(exc)


def test_queue_chapter_loop_defaults_to_outline_end():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=1)

    tasks = service.queue_chapter_loop(project.project_id)

    # Should queue chapters 2 and 3 (outline end)
    assert len(tasks) == 14  # 2 chapters * 7 critical-path tasks each



def test_queue_chapter_loop_uses_branch_outline_and_queues_branch_tasks():
    store, _, service = _build_store_and_service()
    project = store.create_project(Project(idea="branch chapter loop", branches=["main", "alt"]))
    project.latest_chapter_cursor = 1
    store.save_project(project)
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            branch="main",
            structured_data={"chapters": [{"chapter_number": 1}, {"chapter_number": 2}, {"chapter_number": 3}]},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.brief,
            branch="alt",
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            branch="alt",
            structured_data={"chapters": [{"chapter_number": 1}, {"chapter_number": 2}]},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.final_chapter,
            branch="alt",
            structured_data={"chapter_number": 1},
        )
    )
    for asset_type in BOOTSTRAP_ASSET_TYPES:
        store.save_asset(
            Asset(
                project_id=project.project_id,
                asset_type=asset_type,
                branch="alt",
            )
        )

    tasks = service.queue_chapter_loop(project.project_id, branch="alt")

    assert len(tasks) == 7
    assert [task.task_type for task in tasks] == NEXT_CHAPTER_TASK_TYPES
    assert {task.branch for task in tasks} == {"alt"}
    assert {task.payload["branch"] for task in tasks} == {"alt"}
    assert {task.payload["chapter_number"] for task in tasks} == {2}


def test_audit_revision_chain_freezes_quality_gate_inputs():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    queued = service.queue_first_loop(project.project_id, chapter_number=1)

    service.drain(project.project_id)

    tasks = store.list_tasks(project.project_id)
    by_type = {task.task_type: task for task in tasks}
    revision_task = by_type[TaskType.chapter_revision]
    reaudit_task = by_type[TaskType.chapter_reaudit]
    final_task = by_type[TaskType.final_save]
    export_task = by_type[TaskType.export_candidate]

    draft_ref = by_type[TaskType.chapter_generation].output_refs[0]
    validation_ref = by_type[TaskType.chapter_validation].output_refs[0]
    audit_ref = by_type[TaskType.chapter_audit].output_refs[0]
    revised_ref, delta_ref = revision_task.output_refs
    reaudit_ref = reaudit_task.output_refs[0]
    final_ref = final_task.output_refs[0]

    assert [task.task_type for task in queued] == [
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
    assert {draft_ref, validation_ref, audit_ref}.issubset(revision_task.input_asset_refs)
    assert {revised_ref, delta_ref, validation_ref, audit_ref}.issubset(reaudit_task.input_asset_refs)
    assert {revised_ref, delta_ref, reaudit_ref}.issubset(final_task.input_asset_refs)
    assert export_task.input_asset_refs == [final_ref]
