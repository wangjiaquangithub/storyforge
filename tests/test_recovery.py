from datetime import timedelta

from fastapi.testclient import TestClient

from storyforge.domain.models import Asset, AssetType, TaskStatus, utc_now

from storyforge.api.app import create_app
from storyforge.execution.store import InMemoryStoryForgeStore


def test_queued_child_is_cancelled_when_parent_fails() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project = client.post("/api/projects", json={"idea": "A broken moon learns to sing."}).json()
        project_id = project["project_id"]

        client.post(f"/api/projects/{project_id}/runs/first-loop", json={"chapter_number": 1})
        first = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert first.status_code == 200
        assert first.json()["status"] == "completed"

        brief = store.get_latest_asset(project_id, AssetType.brief, branch="main")
        assert brief is not None
        store.delete_asset(brief.asset_id)

        drain_response = client.post(f"/api/projects/{project_id}/workers/drain")
        assert drain_response.status_code == 200

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        by_type = {task["task_type"]: task for task in tasks}
        assert by_type["outline_generation"]["status"] == "failed"
        assert by_type["chapter_generation"]["status"] == "cancelled"
        assert "Parent task" in by_type["chapter_generation"]["error"]
        assert by_type["chapter_review"]["status"] == "cancelled"

        child_events = client.get(f"/api/tasks/{by_type['chapter_generation']['task_id']}/events").json()
        assert child_events[-1]["event_type"] == "cancelled"
        assert child_events[-1]["payload"]["parent_status"] == "failed"


def test_queued_child_is_cancelled_when_parent_is_cancelled() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project = client.post("/api/projects", json={"idea": "A city of bells falls silent."}).json()
        project_id = project["project_id"]

        tasks = client.post(f"/api/projects/{project_id}/runs/first-loop", json={"chapter_number": 1}).json()
        outline_task = next(task for task in tasks if task["task_type"] == "outline_generation")
        chapter_task = next(task for task in tasks if task["task_type"] == "chapter_generation")

        cancel_response = client.post(f"/api/tasks/{outline_task['task_id']}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"

        process_response = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert process_response.status_code == 200
        assert process_response.json()["task_type"] == "brief_generation"

        second_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert second_process.status_code == 200
        assert second_process.json() is None

        stored_child = store.get_task(chapter_task["task_id"])
        assert stored_child is not None
        assert stored_child.status == TaskStatus.cancelled
        assert "Parent task" in (stored_child.error or "")


def test_cancel_queued_task() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post("/api/projects", json={"idea": "A ghost architect rebuilds a dead empire."}).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={"task_type": "brief_generation", "payload": {}},
        ).json()
        task_id = task["task_id"]

        cancel_response = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"

        events_response = client.get(f"/api/tasks/{task_id}/events")
        event_types = [event["event_type"] for event in events_response.json()]
        assert event_types == ["accepted", "queued", "cancelled"]


def test_retry_after_waiting_retry_and_terminal_failure() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post("/api/projects", json={"idea": "A machine monk rewrites fate."}).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "task_type": "chapter_generation",
                "payload": {"chapter_number": 99},
                "config": {"retry_limit": 1},
            },
        ).json()
        task_id = task["task_id"]

        first_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert first_process.status_code == 200
        assert first_process.json()["status"] == "waiting_retry"
        assert first_process.json()["retry_count"] == 1

        retry_response = client.post(f"/api/tasks/{task_id}/retry")
        assert retry_response.status_code == 200
        assert retry_response.json()["status"] == "queued"

        second_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert second_process.status_code == 200
        assert second_process.json()["status"] == "failed"

        events_response = client.get(f"/api/tasks/{task_id}/events")
        event_types = [event["event_type"] for event in events_response.json()]
        assert event_types == [
            "accepted",
            "queued",
            "started",
            "waiting_retry",
            "queued",
            "started",
            "failed",
        ]


def test_waiting_retry_task_auto_requeues_after_retry_delay() -> None:
    store = InMemoryStoryForgeStore()
    with TestClient(create_app(store=store)) as client:
        project = client.post("/api/projects", json={"idea": "A bellmaker rewrites storms."}).json()
        project_id = project["project_id"]

        task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "task_type": "chapter_generation",
                "payload": {"chapter_number": 99},
                "config": {"retry_limit": 2, "values": {"retry_delay_seconds": 60}},
            },
        ).json()
        task_id = task["task_id"]

        first_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert first_process.status_code == 200
        first_body = first_process.json()
        assert first_body["status"] == "waiting_retry"
        first_ready_at = first_body["effective_config_snapshot"]["values"]["retry_ready_at"]

        delayed_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert delayed_process.status_code == 200
        assert delayed_process.json() is None

        stored_task = store.get_task(task_id)
        assert stored_task is not None
        stored_task.effective_config_snapshot.values["retry_ready_at"] = (utc_now() - timedelta(seconds=1)).isoformat()
        store.save_task(stored_task)

        retry_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert retry_process.status_code == 200
        retry_body = retry_process.json()
        assert retry_body["status"] == "waiting_retry"
        second_ready_at = retry_body["effective_config_snapshot"]["values"]["retry_ready_at"]
        assert second_ready_at != first_ready_at

        delayed_second_retry = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert delayed_second_retry.status_code == 200
        assert delayed_second_retry.json() is None

        stored_task = store.get_task(task_id)
        assert stored_task is not None
        stored_task.effective_config_snapshot.values["retry_ready_at"] = "not-a-datetime"
        store.save_task(stored_task)

        malformed_ready_at = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert malformed_ready_at.status_code == 200
        assert malformed_ready_at.json() is None

        stored_task = store.get_task(task_id)
        assert stored_task is not None
        stored_task.effective_config_snapshot.values["retry_ready_at"] = (utc_now() - timedelta(seconds=1)).isoformat()
        store.save_task(stored_task)

        final_process = client.post(f"/api/projects/{project_id}/workers/process-next")
        assert final_process.status_code == 200
        assert final_process.json()["status"] == "failed"

        events_response = client.get(f"/api/tasks/{task_id}/events")
        event_types = [event["event_type"] for event in events_response.json()]
        assert event_types == [
            "accepted",
            "queued",
            "started",
            "waiting_retry",
            "queued",
            "started",
            "waiting_retry",
            "queued",
            "started",
            "failed",
        ]
