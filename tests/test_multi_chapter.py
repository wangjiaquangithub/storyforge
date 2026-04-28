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


def _seed_completed_chapter(store, service, project, chapter_number=1):
    """Seed a completed chapter pipeline (brief + outline + chapter + review)."""
    brief = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.brief_generation,
        payload={"chapter_number": chapter_number},
    ))
    brief.effective_config_snapshot.retry_limit = 0
    store.save_task(brief)
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, content="brief"))

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
        asset_type=AssetType.chapter,
        content="chapter text",
        structured_data={"chapter_number": chapter_number, "title": f"Ch{chapter_number}"},
    ))

    review = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.chapter_review,
        payload={"chapter_number": chapter_number},
        parent_task_id=chapter.task_id,
    ))
    review.effective_config_snapshot.retry_limit = 0
    store.save_task(review)
    store.save_asset(Asset(
        project_id=project.project_id,
        asset_type=AssetType.review_note,
        content="review",
        structured_data={"chapter_number": chapter_number, "approved": True},
    ))

    for task in [brief, outline, chapter, review]:
        service.state_machine.transition(task, TaskStatus.queued, step="queued", progress=0.0)
        service.state_machine.transition(task, TaskStatus.running, step="running", progress=0.1)
        service.state_machine.transition(task, TaskStatus.completed, step="completed", progress=1.0)
        store.save_task(task)

    project.latest_chapter_cursor = chapter_number
    project.current_phase = ProjectPhase.review
    store.save_project(project)


def test_queue_next_chapter_queues_chapter_and_review_from_existing_state():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=1)

    brief_asset = store.get_latest_asset(project.project_id, AssetType.brief)
    outline_asset = store.get_latest_asset(project.project_id, AssetType.outline)
    tasks = service.queue_next_chapter(project.project_id)

    assert len(tasks) == 2
    assert tasks[0].task_type == TaskType.chapter_generation
    assert tasks[1].task_type == TaskType.chapter_review
    assert tasks[0].payload["chapter_number"] == 2
    assert tasks[0].parent_task_id is None
    assert tasks[0].input_asset_refs == [outline_asset.asset_id, brief_asset.asset_id]
    assert tasks[1].parent_task_id == tasks[0].task_id


def test_queue_next_chapter_expands_outline_when_exhausted():
    store, _, service = _build_store_and_service()
    project = _create_project(store)
    _seed_completed_chapter(store, service, project, chapter_number=3)

    # Outline only has chapters 1-3, cursor is at 3
    tasks = service.queue_next_chapter(project.project_id)

    assert len(tasks) == 2
    assert tasks[0].task_type == TaskType.chapter_generation
    assert tasks[1].task_type == TaskType.chapter_review
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

    # 2 chapters (2, 3) * 2 tasks each = 4 tasks
    assert len(tasks) == 4


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
    assert len(tasks) == 4  # 2 chapters * 2 tasks each



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
            asset_type=AssetType.chapter,
            branch="alt",
            structured_data={"chapter_number": 1},
        )
    )

    tasks = service.queue_chapter_loop(project.project_id, branch="alt")

    assert len(tasks) == 2
    assert {task.branch for task in tasks} == {"alt"}
    assert {task.payload["branch"] for task in tasks} == {"alt"}
    assert {task.payload["chapter_number"] for task in tasks} == {2}


def test_review_reject_queues_rewrite():
    store, _, service = _build_store_and_service()
    project = _create_project(store)

    # Seed a completed chapter pipeline with review that FAILS
    brief = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.brief_generation,
    ))
    brief.effective_config_snapshot.retry_limit = 0
    store.save_task(brief)

    outline = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.outline_generation,
        parent_task_id=brief.task_id,
    ))
    outline.effective_config_snapshot.retry_limit = 0
    store.save_task(outline)

    chapter_task = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.chapter_generation,
        payload={"chapter_number": 1},
        parent_task_id=outline.task_id,
    ))
    chapter_task.effective_config_snapshot.retry_limit = 0
    store.save_task(chapter_task)
    brief_asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, content="brief"))
    outline_asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.outline, content="outline"))
    chapter_asset = Asset(
        project_id=project.project_id,
        asset_type=AssetType.chapter,
        content="original chapter",
        structured_data={"chapter_number": 1, "title": "Ch1"},
    )
    store.save_asset(chapter_asset)

    review_task = store.create_task(TaskRecord(
        project_id=project.project_id,
        task_type=TaskType.chapter_review,
        payload={"chapter_number": 1},
        parent_task_id=chapter_task.task_id,
        input_asset_refs=[chapter_asset.asset_id],
    ))
    review_task.effective_config_snapshot.retry_limit = 0
    store.save_task(review_task)

    review_asset = Asset(
        project_id=project.project_id,
        asset_type=AssetType.review_note,
        content="review",
        structured_data={
            "chapter_number": 1,
            "approved": False,
            "issues": ["Pacing is too slow", "Character motivation unclear"],
        },
    )
    store.save_asset(review_asset)

    for task in [brief, outline, chapter_task, review_task]:
        service.state_machine.transition(task, TaskStatus.queued, step="queued", progress=0.0)
        service.state_machine.transition(task, TaskStatus.running, step="running", progress=0.1)
        service.state_machine.transition(task, TaskStatus.completed, step="completed", progress=1.0)
        store.save_task(task)

    # Mark project state
    project.latest_chapter_cursor = 1
    project.current_phase = ProjectPhase.drafting
    store.save_project(project)

    # Reset review_task to queued so _execute can transition it properly
    review_task.status = TaskStatus.queued
    store.save_task(review_task)

    # Now execute the review task through _execute (which calls _handle_review_continuation)
    result = service._execute(review_task)

    # Check that a rewrite task was queued
    all_tasks = store.list_tasks(project.project_id)
    rewrite_tasks = [t for t in all_tasks if t.payload.get("rewrite")]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].status == TaskStatus.queued
    assert chapter_asset.asset_id in rewrite_tasks[0].input_asset_refs
    assert outline_asset.asset_id in rewrite_tasks[0].input_asset_refs
    assert brief_asset.asset_id in rewrite_tasks[0].input_asset_refs
    follow_up_review_tasks = [
        t
        for t in all_tasks
        if t.task_type == TaskType.chapter_review and t.parent_task_id == rewrite_tasks[0].task_id
    ]
    assert len(follow_up_review_tasks) == 1
    assert follow_up_review_tasks[0].status == TaskStatus.queued
    assert follow_up_review_tasks[0].payload["rewrite_review"] is True
    # Issues come from the fallback review (structural checks) since no LLM is configured
    assert "outline reference" in str(rewrite_tasks[0].payload["review_issues"])
