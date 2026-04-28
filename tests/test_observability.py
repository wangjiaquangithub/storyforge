from fastapi.testclient import TestClient

from storyforge.api.app import create_app
from storyforge.execution.store import InMemoryStoryForgeStore


def test_project_summary_reports_completed_pipeline() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project = client.post(
            "/api/projects",
            json={"idea": "A dead ruler rebuilds a kingdom beneath the sea."},
        ).json()
        project_id = project["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1, "auto_run": True},
        )

        summary_response = client.get(f"/api/projects/{project_id}/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()

        assert summary["task_summary"]["total"] == 4
        assert summary["task_summary"]["completed"] == 4
        assert summary["task_summary"]["failed"] == 0
        assert summary["failure_summary"]["failed_task_ids"] == []
        assert summary["latest_event_type"] == "completed"


def test_project_summary_reports_retryable_failure() -> None:
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

        client.post(f"/api/projects/{project_id}/workers/process-next")

        summary_response = client.get(f"/api/projects/{project_id}/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()

        assert summary["task_summary"]["waiting_retry"] == 1
        assert summary["failure_summary"]["waiting_retry_task_ids"] == [task["task_id"]]
        assert task["task_id"] in summary["failure_summary"]["last_error_by_task"]
        assert summary["latest_task_id"] == task["task_id"]
        assert summary["latest_event_type"] == "waiting_retry"
