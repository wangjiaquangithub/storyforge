from fastapi.testclient import TestClient

from storyforge.api.app import create_app
from storyforge.domain.models import Asset, AssetType, TaskRecord, TaskStatus, TaskType
from storyforge.execution.store import InMemoryStoryForgeStore


def test_downstream_tasks_record_correct_input_asset_refs() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A ghost architect rebuilds a dead empire."},
        ).json()
        project_id = project["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        client.post(f"/api/projects/{project_id}/workers/drain")

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        task_by_type = {t["task_type"]: t for t in tasks}

        # brief has no upstream deps
        assert task_by_type["brief_generation"]["input_asset_refs"] == []

        # outline depends on brief
        outline_refs = task_by_type["outline_generation"]["input_asset_refs"]
        assert len(outline_refs) == 1
        brief_asset_id = task_by_type["brief_generation"]["output_refs"][0]
        assert brief_asset_id in outline_refs

        # chapter depends on outline and brief
        chapter_refs = task_by_type["chapter_generation"]["input_asset_refs"]
        assert len(chapter_refs) == 2
        outline_asset_id = task_by_type["outline_generation"]["output_refs"][0]
        assert outline_asset_id in chapter_refs
        assert brief_asset_id in chapter_refs

        # review depends on chapter
        review_refs = task_by_type["chapter_review"]["input_asset_refs"]
        assert len(review_refs) == 1
        chapter_asset_id = task_by_type["chapter_generation"]["output_refs"][0]
        assert chapter_asset_id in review_refs


def test_cross_project_input_asset_ref_is_rejected() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project_a = client.post("/api/projects", json={"idea": "A lantern keeper charts frozen dreams."}).json()
        project_b = client.post("/api/projects", json={"idea": "A mirror knight defends a sleeping city."}).json()

        foreign_brief = store.save_asset(Asset(project_id=project_b["project_id"], asset_type=AssetType.brief, content="foreign brief"))
        local_outline = store.save_asset(Asset(project_id=project_a["project_id"], asset_type=AssetType.outline, content="local outline"))
        task = TaskRecord(
            project_id=project_a["project_id"],
            task_type=TaskType.chapter_generation,
            branch="main",
            input_asset_refs=[local_outline.asset_id, foreign_brief.asset_id],
            payload={"chapter_number": 1},
            status=TaskStatus.accepted,
        )
        store.create_task(task)
        task.status = TaskStatus.queued
        store.save_task(task)

        response = client.post(f"/api/projects/{project_a['project_id']}/workers/process-next")

        assert response.status_code == 200
        processed = response.json()
        assert processed["status"] == "failed"
        assert processed["error"] == "Input asset not found"


def test_cross_project_rewrite_original_asset_id_is_rejected_at_queue_time() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project_a = client.post("/api/projects", json={"idea": "A mapmaker bargains with thunder."}).json()
        project_b = client.post("/api/projects", json={"idea": "A river oracle hides a crown."}).json()

        foreign_chapter = store.save_asset(Asset(project_id=project_b["project_id"], asset_type=AssetType.chapter, content="foreign chapter"))
        response = client.post(
            f"/api/projects/{project_a['project_id']}/tasks",
            json={
                "task_type": "chapter_generation",
                "payload": {"rewrite": True, "original_asset_id": foreign_chapter.asset_id, "chapter_number": 1},
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Original chapter not found"


def test_cross_project_rewrite_original_asset_id_is_rejected_at_execution_time() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project_a = client.post("/api/projects", json={"idea": "A glass forest remembers every war."}).json()
        project_b = client.post("/api/projects", json={"idea": "A moon sailor steals the tide."}).json()

        local_brief = store.save_asset(Asset(project_id=project_a["project_id"], asset_type=AssetType.brief, content="local brief"))
        local_outline = store.save_asset(Asset(project_id=project_a["project_id"], asset_type=AssetType.outline, content="local outline"))
        foreign_chapter = store.save_asset(Asset(project_id=project_b["project_id"], asset_type=AssetType.chapter, content="foreign chapter"))
        task = TaskRecord(
            project_id=project_a["project_id"],
            task_type=TaskType.chapter_generation,
            branch="main",
            input_asset_refs=[local_outline.asset_id, local_brief.asset_id],
            payload={"rewrite": True, "original_asset_id": foreign_chapter.asset_id, "chapter_number": 1},
            status=TaskStatus.accepted,
        )
        store.create_task(task)
        task.status = TaskStatus.queued
        store.save_task(task)

        response = client.post(f"/api/projects/{project_a['project_id']}/workers/process-next")

        assert response.status_code == 200
        processed = response.json()
        assert processed["status"] == "failed"
        assert processed["error"] == "Original chapter not found"


def test_required_inputs_do_not_fall_back_to_latest_asset_after_freeze() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project = client.post("/api/projects", json={"idea": "A clockmaker hides a kingdom in ash."}).json()
        project_id = project["project_id"]

        frozen_brief = store.save_asset(Asset(project_id=project_id, asset_type=AssetType.brief, content="frozen brief"))
        frozen_outline = store.save_asset(Asset(project_id=project_id, asset_type=AssetType.outline, content="frozen outline"))
        store.save_asset(Asset(project_id=project_id, asset_type=AssetType.brief, content="newer brief"))
        store.save_asset(Asset(project_id=project_id, asset_type=AssetType.outline, content="newer outline"))
        store.delete_asset(frozen_outline.asset_id)

        task = TaskRecord(
            project_id=project_id,
            task_type=TaskType.chapter_generation,
            branch="main",
            input_asset_refs=[frozen_outline.asset_id, frozen_brief.asset_id],
            payload={"chapter_number": 1},
            status=TaskStatus.accepted,
        )
        store.create_task(task)
        task.status = TaskStatus.queued
        store.save_task(task)

        response = client.post(f"/api/projects/{project_id}/workers/process-next")

        assert response.status_code == 200
        processed = response.json()
        assert processed["status"] == "failed"
        assert processed["error"] == "Input asset not found"


def test_retry_uses_frozen_input_not_latest_asset() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A ruined archivist binds stars into a new law."},
        ).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "task_type": "chapter_generation",
                "payload": {"chapter_number": 88},
                "config": {"retry_limit": 1},
            },
        ).json()
        task_id = task["task_id"]

        client.post(f"/api/projects/{project_id}/workers/process-next")

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        first_run_task = next(t for t in tasks if t["task_id"] == task_id)
        frozen_refs = first_run_task["input_asset_refs"]

        client.post(f"/api/tasks/{task_id}/retry")
        client.post(f"/api/projects/{project_id}/workers/process-next")

        tasks_after = client.get(f"/api/projects/{project_id}/tasks").json()
        retried_task = next(t for t in tasks_after if t["task_id"] == task_id)
        assert retried_task["input_asset_refs"] == frozen_refs


def test_review_binds_to_correct_chapter_output() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A dead ruler rebuilds a kingdom beneath the sea."},
        ).json()
        project_id = project["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        client.post(f"/api/projects/{project_id}/workers/drain")

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        task_by_type = {t["task_type"]: t for t in tasks}

        review_task = task_by_type["chapter_review"]
        chapter_output = task_by_type["chapter_generation"]["output_refs"][0]

        assert chapter_output in review_task["input_asset_refs"]

        review_asset_id = review_task["output_refs"][0]
        assets = client.get(f"/api/projects/{project_id}/assets").json()
        review_asset = next(a for a in assets if a["asset_id"] == review_asset_id)
        assert review_asset["structured_data"]["chapter_ref"] == chapter_output
        assert review_asset["structured_data"]["approved"] is True
        assert "issues" in review_asset["structured_data"]


def test_partial_failure_leaves_project_state_consistent() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A machine monk rewrites fate."},
        ).json()
        project_id = project["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        client.post(f"/api/projects/{project_id}/workers/drain")

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        assert all(t["status"] == "completed" for t in tasks)

        project_state = client.get(f"/api/projects/{project_id}").json()
        assert project_state["current_phase"] == "review"
        assert project_state["latest_chapter_cursor"] == 1

        assets = client.get(f"/api/projects/{project_id}/assets").json()
        assert len(assets) == 4


def test_config_snapshot_is_frozen_at_queue_time() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A lost cartographer maps the void between worlds."},
        ).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "task_type": "brief_generation",
                "payload": {},
                "config": {
                    "provider": "custom-provider",
                    "model": "custom-model",
                    "retry_limit": 5,
                    "review_policy": "strict",
                },
            },
        ).json()

        assert task["effective_config_snapshot"]["provider"] == "custom-provider"
        assert task["effective_config_snapshot"]["model"] == "custom-model"
        assert task["effective_config_snapshot"]["retry_limit"] == 5
        assert task["effective_config_snapshot"]["review_policy"] == "strict"



def test_config_snapshot_preserves_zero_retry_limit() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A producer disables retries for a clean experiment run."},
        ).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "task_type": "chapter_review",
                "payload": {"chapter_number": 1},
                "config": {
                    "retry_limit": 0,
                    "review_policy": "strict",
                },
            },
        ).json()

        assert task["effective_config_snapshot"]["retry_limit"] == 0
        assert task["effective_config_snapshot"]["review_policy"] == "strict"
