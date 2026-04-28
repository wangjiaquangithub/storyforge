import json
import threading
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from storyforge.api.app import create_app
from storyforge.domain.models import Project, ProjectExecutionClaim, RuntimeAuditRecord, utc_now
from storyforge.execution.runtime import WorkerRuntime
from storyforge.execution.state_machine import TaskStateMachine
from storyforge.execution.store import InMemoryStoryForgeStore
from storyforge.execution.workflow import ClosedLoopService
from storyforge.persistence.sqlite_store import SQLiteStoryForgeStore


def test_auto_run_first_loop_completes_inline() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A broken saint forges a second heaven underground."},
        )
        project_id = project_response.json()["project_id"]

        run_response = client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1, "auto_run": True},
        )
        assert run_response.status_code == 200

        tasks_response = client.get(f"/api/projects/{project_id}/tasks")
        assert tasks_response.status_code == 200
        assert all(task["status"] == "completed" for task in tasks_response.json())


def test_background_runtime_processes_enqueued_project(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"

    with TestClient(create_app(db_path=str(db_path))) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "An abandoned prince restarts civilization through forbidden engines."},
        )
        project_id = project_response.json()["project_id"]

        queue_response = client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        assert queue_response.status_code == 200

        enqueue_response = client.post(f"/api/projects/{project_id}/runtime/enqueue")
        assert enqueue_response.status_code == 200
        assert enqueue_response.json()["mode"] == "background"

        deadline = time.time() + 2.0
        while time.time() < deadline:
            tasks_response = client.get(f"/api/projects/{project_id}/tasks")
            tasks = tasks_response.json()
            if tasks and all(task["status"] == "completed" for task in tasks):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Background worker did not complete tasks in time")

        assets_response = client.get(f"/api/projects/{project_id}/assets")
        assert assets_response.status_code == 200
        assert len(assets_response.json()) == 4



def test_project_event_stream_emits_live_task_events() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A gravekeeper steals tomorrow from the empire's dead sun."},
        )
        project_id = project_response.json()["project_id"]

        task_response = client.post(
            f"/api/projects/{project_id}/tasks",
            json={"task_type": "brief_generation", "payload": {"mode": "initial"}},
        )
        assert task_response.status_code == 200
        task_id = task_response.json()["task_id"]

        stream_response = client.get(f"/api/projects/{project_id}/events?max_events=2")
        assert stream_response.status_code == 200
        lines = [line for line in stream_response.text.splitlines() if line and not line.startswith(":")]

        assert lines[0].startswith("id: evt_")
        assert lines[1] == "event: accepted"
        assert lines[2].startswith("data: ")
        payload = json.loads(lines[2][6:])
        assert payload["project_id"] == project_id
        assert payload["task_id"] == task_id
        assert payload["event_type"] == "accepted"
        assert lines[3].startswith("id: evt_")
        assert lines[4] == "event: queued"

        events_response = client.get(f"/api/tasks/{task_id}/events")
        assert events_response.status_code == 200
        assert [event["event_type"] for event in events_response.json()[:2]] == ["accepted", "queued"]



def test_project_event_stream_can_resume_after_event_id() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A mapmaker rewrites causality with drowned constellations."},
        )
        project_id = project_response.json()["project_id"]

        task_response = client.post(
            f"/api/projects/{project_id}/tasks",
            json={"task_type": "brief_generation", "payload": {"mode": "initial"}},
        )
        task_id = task_response.json()["task_id"]

        initial_stream = client.get(f"/api/projects/{project_id}/events?max_events=2")
        initial_lines = [line for line in initial_stream.text.splitlines() if line and not line.startswith(":")]
        first_event_id = initial_lines[0][4:]

        resumed_stream = client.get(f"/api/projects/{project_id}/events?after_event_id={first_event_id}&max_events=1")
        resumed_lines = [line for line in resumed_stream.text.splitlines() if line and not line.startswith(":")]

        assert resumed_lines[0].startswith("id: evt_")
        assert resumed_lines[1] == "event: queued"
        payload = json.loads(resumed_lines[2][6:])
        assert payload["task_id"] == task_id
        assert payload["event_type"] == "queued"



def test_project_event_stream_honors_last_event_id_header() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A blind regent feeds history back into a machine oracle."},
        )
        project_id = project_response.json()["project_id"]

        client.post(
            f"/api/projects/{project_id}/tasks",
            json={"task_type": "brief_generation", "payload": {"mode": "initial"}},
        )

        initial_stream = client.get(f"/api/projects/{project_id}/events?max_events=2")
        initial_lines = [line for line in initial_stream.text.splitlines() if line and not line.startswith(":")]
        first_event_id = initial_lines[0][4:]

        resumed_stream = client.get(
            f"/api/projects/{project_id}/events?max_events=1",
            headers={"Last-Event-ID": first_event_id},
        )
        resumed_lines = [line for line in resumed_stream.text.splitlines() if line and not line.startswith(":")]

        assert resumed_lines[1] == "event: queued"
        payload = json.loads(resumed_lines[2][6:])
        assert payload["event_type"] == "queued"



def test_project_event_stream_rejects_invalid_after_timestamp() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A memory-thief turns prophecy into an industrial fuel."},
        )
        project_id = project_response.json()["project_id"]

        response = client.get(f"/api/projects/{project_id}/events?after_timestamp=not-a-date")
        assert response.status_code == 400
        assert response.json()["detail"] == "after_timestamp must be a valid ISO 8601 datetime"



def test_background_runtime_deduplicates_same_project_enqueue(tmp_path: Path) -> None:
    db_path = tmp_path / "dedupe.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A cursed surveyor rebuilds empire roads across a dead god."},
        )
        project_id = project_response.json()["project_id"]

        queue_response = client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        assert queue_response.status_code == 200

        client.post(f"/api/projects/{project_id}/runtime/enqueue")
        client.post(f"/api/projects/{project_id}/runtime/enqueue")
        client.post(f"/api/projects/{project_id}/runtime/enqueue")

        deadline = time.time() + 2.0
        while time.time() < deadline:
            tasks = client.get(f"/api/projects/{project_id}/tasks").json()
            if tasks and all(task["status"] == "completed" for task in tasks):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Background worker did not complete deduplicated project queue in time")

        events_by_task = [client.get(f"/api/tasks/{task['task_id']}/events").json() for task in tasks]
        started_counts = [sum(1 for event in events if event["event_type"] == "started") for events in events_by_task]
        assert started_counts == [1, 1, 1, 1]



def test_process_now_serializes_with_background_drain(tmp_path: Path) -> None:
    db_path = tmp_path / "coordination.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A dispossessed archivist bootstraps a throne from machine scripture."},
        )
        project_id = project_response.json()["project_id"]

        queue_response = client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )
        assert queue_response.status_code == 200

        client.post(f"/api/projects/{project_id}/runtime/enqueue")

        result_holder: dict[str, object] = {}

        def run_process_now() -> None:
            result_holder["response"] = client.post(f"/api/projects/{project_id}/runtime/process-now")

        thread = threading.Thread(target=run_process_now)
        thread.start()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

        response = result_holder["response"]
        assert response.status_code == 200

        deadline = time.time() + 2.0
        while time.time() < deadline:
            tasks = client.get(f"/api/projects/{project_id}/tasks").json()
            if tasks and all(task["status"] == "completed" for task in tasks):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Project coordination did not finish in time")

        events_by_task = [client.get(f"/api/tasks/{task['task_id']}/events").json() for task in tasks]
        started_counts = [sum(1 for event in events if event["event_type"] == "started") for events in events_by_task]
        completed_counts = [sum(1 for event in events if event["event_type"] == "completed") for events in events_by_task]
        assert started_counts == [1, 1, 1, 1]
        assert completed_counts == [1, 1, 1, 1]



def test_runtime_skips_project_with_active_foreign_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "claims.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "An oathbreaker powers a republic with stolen divine weather."},
        )
        project_id = project_response.json()["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )

        response = client.post(f"/api/projects/{project_id}/runtime/process-now")
        assert response.status_code == 200
        assert response.json()["processed_count"] == 0

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        assert all(task["status"] == "queued" for task in tasks)
        claim = store.get_project_claim(project_id)
        assert claim is not None
        assert claim.worker_id == "worker_foreign"



def test_runtime_reclaims_project_after_expired_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "expired-claims.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A tomb-engineer finances resurrection through imperial debt markets."},
        )
        project_id = project_response.json()["project_id"]

        client.post(
            f"/api/projects/{project_id}/runs/first-loop",
            json={"chapter_number": 1},
        )

        expired_at = utc_now() - timedelta(seconds=5)
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_dead",
                claimed_at=expired_at,
                lease_expires_at=expired_at,
            )
        )

        response = client.post(f"/api/projects/{project_id}/runtime/process-now")
        assert response.status_code == 200
        assert response.json()["processed_count"] == 4

        tasks = client.get(f"/api/projects/{project_id}/tasks").json()
        assert all(task["status"] == "completed" for task in tasks)
        assert store.get_project_claim(project_id) is None



def test_runtime_claim_endpoint_returns_current_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "inspect-claim.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A siege accountant turns famine logistics into imperial leverage."},
        )
        project_id = project_response.json()["project_id"]

        now = utc_now()
        claim = store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_foreign",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=30),
                lease_duration_seconds=30.0,
                heartbeat_interval_seconds=10.0,
                last_heartbeat_at=now,
            )
        )

        response = client.get(f"/api/projects/{project_id}/runtime/claim")
        assert response.status_code == 200
        body = response.json()["claim"]
        assert body["project_id"] == project_id
        assert body["worker_id"] == claim.worker_id
        assert body["lease_duration_seconds"] == 30.0
        assert body["heartbeat_interval_seconds"] == 10.0
        assert body["last_heartbeat_at"] is not None
        assert body["heartbeat_overdue"] is False
        assert body["heartbeat_age_seconds"] is not None



def test_runtime_heartbeat_renews_owned_claim_lease() -> None:
    store = InMemoryStoryForgeStore()
    workflow = ClosedLoopService(store, TaskStateMachine())
    runtime = WorkerRuntime(workflow)
    runtime._lease_seconds = 1
    runtime._heartbeat_interval_seconds = 0.1

    project = store.create_project(
        Project(
            idea="A ghost broker arbitrages empires with delayed funerals."
        )
    )
    original_claim = store.save_project_claim(
        ProjectExecutionClaim(
            project_id=project.project_id,
            worker_id=runtime.worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=0.2),
        )
    )
    original_expiry = original_claim.lease_expires_at

    heartbeat = runtime._start_claim_heartbeat(project.project_id)
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            renewed_claim = store.get_project_claim(project.project_id)
            assert renewed_claim is not None
            if renewed_claim.lease_expires_at > original_expiry + timedelta(seconds=0.4):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Claim heartbeat did not extend lease expiry in time")
    finally:
        heartbeat.set()



def test_runtime_release_owned_claim_preserves_foreign_takeover() -> None:
    store = InMemoryStoryForgeStore()
    workflow = ClosedLoopService(store, TaskStateMachine())
    runtime = WorkerRuntime(workflow)

    project = store.create_project(
        Project(
            idea="A tithe smuggler monetizes sainthood through border courts."
        )
    )
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=project.project_id,
            worker_id=runtime.worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=30),
        )
    )
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=project.project_id,
            worker_id="worker_foreign",
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=30),
        )
    )

    runtime._release_owned_project_claim(project.project_id)

    claim = store.get_project_claim(project.project_id)
    assert claim is not None
    assert claim.worker_id == "worker_foreign"



def test_runtime_records_audit_when_claim_heartbeat_loses_deleted_claim() -> None:
    store = InMemoryStoryForgeStore()
    workflow = ClosedLoopService(store, TaskStateMachine())
    runtime = WorkerRuntime(workflow)
    runtime._heartbeat_interval_seconds = 0.1

    project = store.create_project(Project(idea="A relic banker loses empires through a missing ledger."))
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=project.project_id,
            worker_id=runtime.worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=30),
        )
    )

    heartbeat = runtime._start_claim_heartbeat(project.project_id)
    try:
        store.delete_project_claim(project.project_id)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            records = store.list_runtime_audits(project.project_id)
            if records:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Claim heartbeat loss audit was not recorded in time")
    finally:
        heartbeat.set()

    assert len(records) == 1
    record = records[0]
    assert record.action == "claim_heartbeat_lost"
    assert record.project_id == project.project_id
    assert record.actor_worker_id == runtime.worker_id
    assert record.claim_worker_id is None
    assert record.forced is False
    assert record.stale is False



def test_runtime_records_audit_when_claim_heartbeat_loses_foreign_takeover() -> None:
    store = InMemoryStoryForgeStore()
    workflow = ClosedLoopService(store, TaskStateMachine())
    runtime = WorkerRuntime(workflow)
    runtime._heartbeat_interval_seconds = 0.1

    project = store.create_project(Project(idea="A census broker loses sovereignty to a faster clerk."))
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=project.project_id,
            worker_id=runtime.worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=30),
        )
    )

    heartbeat = runtime._start_claim_heartbeat(project.project_id)
    try:
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project.project_id,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )
        deadline = time.time() + 1.0
        while time.time() < deadline:
            records = store.list_runtime_audits(project.project_id)
            if records:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Foreign takeover heartbeat audit was not recorded in time")
    finally:
        heartbeat.set()

    assert len(records) == 1
    record = records[0]
    assert record.action == "claim_heartbeat_lost"
    assert record.project_id == project.project_id
    assert record.actor_worker_id == runtime.worker_id
    assert record.claim_worker_id == "worker_foreign"
    assert record.forced is False
    assert record.stale is False



def test_runtime_claim_release_rejects_foreign_owner_without_force(tmp_path: Path) -> None:
    db_path = tmp_path / "release-claim.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A war cartographer speculates on collapsing borders."},
        )
        project_id = project_response.json()["project_id"]

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )

        response = client.delete(f"/api/projects/{project_id}/runtime/claim")
        assert response.status_code == 409
        assert response.json()["detail"] == "Project claim is owned by another worker"
        assert store.get_project_claim(project_id) is not None



def test_runtime_claim_release_can_force_clear_foreign_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "force-release-claim.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"idea": "A plague judge weaponizes quarantine ledgers against the court."},
        )
        project_id = project_response.json()["project_id"]

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )

        response = client.delete(f"/api/projects/{project_id}/runtime/claim?force=true")
        assert response.status_code == 200
        assert response.json() == {"project_id": project_id, "released": True, "forced": True}
        assert store.get_project_claim(project_id) is None



def test_runtime_claim_list_reports_active_and_stale_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "claim-list.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        active_project = client.post(
            "/api/projects",
            json={"idea": "A magistrate arbitrages rebellions through river toll rights."},
        ).json()["project_id"]
        stale_project = client.post(
            "/api/projects",
            json={"idea": "A salt broker finances war using weather futures."},
        ).json()["project_id"]

        now = utc_now()
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=active_project,
                worker_id="worker_active",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
        )
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=stale_project,
                worker_id="worker_stale",
                claimed_at=now - timedelta(seconds=60),
                lease_expires_at=now - timedelta(seconds=1),
            )
        )

        response = client.get("/api/runtime/claims")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["stale_count"] == 1
        claims_by_project = {claim["project_id"]: claim for claim in body["claims"]}
        assert claims_by_project[active_project]["stale"] is False
        assert claims_by_project[stale_project]["stale"] is True
        assert claims_by_project[active_project]["seconds_until_expiry"] > 0
        assert claims_by_project[stale_project]["seconds_until_expiry"] <= 0



def test_runtime_claim_list_orders_by_lease_expiry(tmp_path: Path) -> None:
    db_path = tmp_path / "claim-order.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        first_project = client.post(
            "/api/projects",
            json={"idea": "A grain auditor topples dynasties with inventory math."},
        ).json()["project_id"]
        second_project = client.post(
            "/api/projects",
            json={"idea": "A lighthouse guild prices immortality as maritime insurance."},
        ).json()["project_id"]

        now = utc_now()
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=second_project,
                worker_id="worker_late",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=20),
            )
        )
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=first_project,
                worker_id="worker_early",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=5),
            )
        )

        response = client.get("/api/runtime/claims")
        assert response.status_code == 200
        body = response.json()
        projects = [claim["project_id"] for claim in body["claims"]]
        assert projects == [first_project, second_project]
        assert body["total"] == 2
        assert body["filtered_total"] == 2
        assert body["offset"] == 0
        assert body["limit"] == 100



def test_runtime_claim_list_supports_filtering_and_pagination(tmp_path: Path) -> None:
    db_path = tmp_path / "claim-filters.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        stale_project = client.post(
            "/api/projects",
            json={"idea": "A border taxonomist turns rebellions into catalogued assets."},
        ).json()["project_id"]
        worker_a_project = client.post(
            "/api/projects",
            json={"idea": "A ration clerk corners empires through famine derivatives."},
        ).json()["project_id"]
        worker_b_project = client.post(
            "/api/projects",
            json={"idea": "A relic insurer brokers miracles as maritime collateral."},
        ).json()["project_id"]

        now = utc_now()
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=stale_project,
                worker_id="worker_a",
                claimed_at=now - timedelta(seconds=60),
                lease_expires_at=now - timedelta(seconds=1),
                lease_duration_seconds=30.0,
                heartbeat_interval_seconds=10.0,
                last_heartbeat_at=now - timedelta(seconds=30),
            )
        )
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=worker_a_project,
                worker_id="worker_a",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
                lease_duration_seconds=30.0,
                heartbeat_interval_seconds=10.0,
                last_heartbeat_at=now - timedelta(seconds=3),
            )
        )
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=worker_b_project,
                worker_id="worker_b",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=20),
                lease_duration_seconds=30.0,
                heartbeat_interval_seconds=4.0,
                last_heartbeat_at=now - timedelta(seconds=12),
            )
        )

        stale_response = client.get("/api/runtime/claims?stale=true")
        assert stale_response.status_code == 200
        stale_body = stale_response.json()
        assert stale_body["total"] == 3
        assert stale_body["filtered_total"] == 1
        assert stale_body["stale_count"] == 1
        assert [claim["project_id"] for claim in stale_body["claims"]] == [stale_project]

        worker_response = client.get("/api/runtime/claims?worker_id=worker_a")
        assert worker_response.status_code == 200
        worker_body = worker_response.json()
        assert worker_body["filtered_total"] == 2
        assert [claim["project_id"] for claim in worker_body["claims"]] == [stale_project, worker_a_project]

        overdue_response = client.get("/api/runtime/claims?heartbeat_overdue=true")
        assert overdue_response.status_code == 200
        overdue_body = overdue_response.json()
        assert overdue_body["filtered_total"] == 2
        assert [claim["project_id"] for claim in overdue_body["claims"]] == [stale_project, worker_b_project]
        assert all(claim["heartbeat_overdue"] is True for claim in overdue_body["claims"])

        paged_response = client.get("/api/runtime/claims?offset=1&limit=1")
        assert paged_response.status_code == 200
        paged_body = paged_response.json()
        assert paged_body["total"] == 3
        assert paged_body["filtered_total"] == 3
        assert paged_body["offset"] == 1
        assert paged_body["limit"] == 1
        assert [claim["project_id"] for claim in paged_body["claims"]] == [worker_a_project]

        project_response = client.get(f"/api/runtime/claims?project_id={worker_b_project}")
        assert project_response.status_code == 200
        project_body = project_response.json()
        assert project_body["filtered_total"] == 1
        assert [claim["project_id"] for claim in project_body["claims"]] == [worker_b_project]
        claim = project_body["claims"][0]
        assert claim["lease_duration_seconds"] == 30.0
        assert claim["heartbeat_interval_seconds"] == 4.0
        assert claim["last_heartbeat_at"] is not None
        assert claim["heartbeat_age_seconds"] is not None
        assert claim["heartbeat_overdue"] is True



def test_bulk_stale_claim_release_clears_only_expired_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "bulk-stale-release.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        active_project = client.post(
            "/api/projects",
            json={"idea": "A tariff scholar weaponizes shipping law against dynasties."},
        ).json()["project_id"]
        stale_project = client.post(
            "/api/projects",
            json={"idea": "A cemetery banker securitizes ancestral debt."},
        ).json()["project_id"]

        now = utc_now()
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=active_project,
                worker_id="worker_active",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
        )
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=stale_project,
                worker_id="worker_stale",
                claimed_at=now - timedelta(seconds=60),
                lease_expires_at=now - timedelta(seconds=1),
            )
        )

        response = client.delete("/api/runtime/claims/stale")
        assert response.status_code == 200
        assert response.json() == {"released_project_ids": [stale_project], "released_count": 1}
        assert store.get_project_claim(stale_project) is None
        assert store.get_project_claim(active_project) is not None



def test_bulk_stale_claim_release_returns_empty_when_nothing_expired(tmp_path: Path) -> None:
    db_path = tmp_path / "bulk-stale-empty.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        active_project = client.post(
            "/api/projects",
            json={"idea": "A canal syndicate rewrites succession through water rights."},
        ).json()["project_id"]

        now = utc_now()
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=active_project,
                worker_id="worker_active",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
        )

        response = client.delete("/api/runtime/claims/stale")
        assert response.status_code == 200
        assert response.json() == {"released_project_ids": [], "released_count": 0}
        assert store.get_project_claim(active_project) is not None



def test_runtime_audits_record_forced_claim_release(tmp_path: Path) -> None:
    db_path = tmp_path / "audit-force-release.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_id = client.post(
            "/api/projects",
            json={"idea": "A debt priest securitizes miracles into court leverage."},
        ).json()["project_id"]

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )

        release_response = client.delete(f"/api/projects/{project_id}/runtime/claim?force=true")
        assert release_response.status_code == 200

        audit_response = client.get(f"/api/runtime/audits?project_id={project_id}")
        assert audit_response.status_code == 200
        body = audit_response.json()
        assert body["total"] == 1
        record = body["records"][0]
        assert record["action"] == "release_project_claim"
        assert record["project_id"] == project_id
        assert record["claim_worker_id"] == "worker_foreign"
        assert record["forced"] is True
        assert record["stale"] is False



def test_runtime_audits_record_bulk_stale_release(tmp_path: Path) -> None:
    db_path = tmp_path / "audit-bulk-release.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_id = client.post(
            "/api/projects",
            json={"idea": "A funeral broker turns inheritance disputes into state capture."},
        ).json()["project_id"]

        expired_at = utc_now() - timedelta(seconds=5)
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_id,
                worker_id="worker_dead",
                claimed_at=expired_at,
                lease_expires_at=expired_at,
            )
        )

        release_response = client.delete("/api/runtime/claims/stale")
        assert release_response.status_code == 200
        assert release_response.json() == {"released_project_ids": [project_id], "released_count": 1}

        audit_response = client.get("/api/runtime/audits")
        assert audit_response.status_code == 200
        body = audit_response.json()
        assert body["total"] == 1
        assert body["filtered_total"] == 1
        assert body["offset"] == 0
        assert body["limit"] == 100
        record = body["records"][0]
        assert record["action"] == "release_stale_project_claim"
        assert record["project_id"] == project_id
        assert record["claim_worker_id"] == "worker_dead"
        assert record["forced"] is True
        assert record["stale"] is True



def test_runtime_audits_include_current_claim_health_summary() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    active_project = store.create_project(Project(idea="A river tithe broker underwrites rebellions as cargo spoilage."))
    stale_project = store.create_project(Project(idea="A shrine auditor collapses dynasties with delayed receipts."))
    overdue_project = store.create_project(Project(idea="A customs monk prices sin as overland tariff debt."))

    now = utc_now()
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=active_project.project_id,
            worker_id="worker_active",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=30),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now,
        )
    )
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=stale_project.project_id,
            worker_id="worker_stale",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=overdue_project.project_id,
            worker_id="worker_overdue",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=30),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=4.0,
            last_heartbeat_at=now - timedelta(seconds=12),
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/audits")
        assert response.status_code == 200
        body = response.json()
        assert body["claim_health_summary"] == {
            "total_claims": 3,
            "stale_claims": 1,
            "heartbeat_overdue_claims": 1,
            "healthy_claims": 1,
            "affected_projects": 3,
            "affected_workers": 3,
        }
        assert body["worker_claim_health_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_stale",
                    "total_claims": 1,
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "healthy_claims": 0,
                    "affected_projects": 1,
                },
                {
                    "worker_id": "worker_overdue",
                    "total_claims": 1,
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "healthy_claims": 0,
                    "affected_projects": 1,
                },
                {
                    "worker_id": "worker_active",
                    "total_claims": 1,
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 0,
                    "healthy_claims": 1,
                    "affected_projects": 1,
                },
            ],
            "total_workers": 3,
        }
        assert body["project_claim_health_summary"] == {
            "projects": [
                {
                    "project_id": stale_project.project_id,
                    "worker_id": "worker_stale",
                    "stale": True,
                    "heartbeat_overdue": False,
                    "seconds_until_expiry": body["project_claim_health_summary"]["projects"][0]["seconds_until_expiry"],
                },
                {
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "stale": False,
                    "heartbeat_overdue": True,
                    "seconds_until_expiry": body["project_claim_health_summary"]["projects"][1]["seconds_until_expiry"],
                },
                {
                    "project_id": active_project.project_id,
                    "worker_id": "worker_active",
                    "stale": False,
                    "heartbeat_overdue": False,
                    "seconds_until_expiry": body["project_claim_health_summary"]["projects"][2]["seconds_until_expiry"],
                },
            ],
            "total_projects": 3,
        }
        assert body["project_claim_health_summary"]["projects"][0]["seconds_until_expiry"] <= 0
        assert body["project_claim_health_summary"]["projects"][1]["seconds_until_expiry"] > 0
        assert body["project_claim_health_summary"]["projects"][2]["seconds_until_expiry"] > 0
        assert body["severity_weights"] == {
            "stale_claim_weight": 100,
            "heartbeat_overdue_weight": 50,
            "lease_loss_weight": 20,
            "forced_release_weight": 10,
            "stale_release_weight": 5,
            "recent_audit_weight": 1,
        }
        assert body["worker_severity_summary"]["workers"][0]["severity_reasons"] == ["1 stale claim"]
        assert body["worker_severity_summary"]["workers"][0]["severity_contributions"] == [
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
        ]
        assert body["project_severity_summary"]["projects"][0]["severity_reasons"] == ["stale current claim"]
        assert body["project_severity_summary"]["projects"][0]["severity_contributions"] == [
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
        ]
        assert body["worker_severity_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["1 stale claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["1 heartbeat-overdue claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "worker_id": "worker_active",
                    "severity_score": 0,
                    "severity_reasons": [],
                    "severity_contributions": [],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
            ],
            "total_workers": 3,
        }
        assert body["project_severity_summary"] == {
            "projects": [
                {
                    "project_id": stale_project.project_id,
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["stale current claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claim": True,
                    "heartbeat_overdue": False,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["heartbeat-overdue current claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claim": False,
                    "heartbeat_overdue": True,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "project_id": active_project.project_id,
                    "worker_id": "worker_active",
                    "severity_score": 0,
                    "severity_reasons": [],
                    "severity_contributions": [],
                    "stale_claim": False,
                    "heartbeat_overdue": False,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
            ],
            "total_projects": 3,
        }
        assert body["joined_severity_summary"] == {
            "items": [
                {
                    "scope": "project",
                    "project_id": stale_project.project_id,
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["stale current claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "worker",
                    "project_id": None,
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["1 stale claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "project",
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["heartbeat-overdue current claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "worker",
                    "project_id": None,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["1 heartbeat-overdue claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "project",
                    "project_id": active_project.project_id,
                    "worker_id": "worker_active",
                    "severity_score": 0,
                    "severity_reasons": [],
                    "severity_contributions": [],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "worker",
                    "project_id": None,
                    "worker_id": "worker_active",
                    "severity_score": 0,
                    "severity_reasons": [],
                    "severity_contributions": [],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
            ],
            "total_items": 6,
        }

        filtered = client.get(
            f"/api/runtime/audits?project_id={overdue_project.project_id}&worker_claim_health_limit=1&project_claim_health_limit=1&worker_severity_limit=1&project_severity_limit=1"
        )
        assert filtered.status_code == 200
        filtered_body = filtered.json()
        assert filtered_body["claim_health_summary"] == {
            "total_claims": 1,
            "stale_claims": 0,
            "heartbeat_overdue_claims": 1,
            "healthy_claims": 0,
            "affected_projects": 1,
            "affected_workers": 1,
        }
        assert filtered_body["worker_claim_health_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_overdue",
                    "total_claims": 1,
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "healthy_claims": 0,
                    "affected_projects": 1,
                }
            ],
            "total_workers": 1,
        }
        assert filtered_body["project_claim_health_summary"] == {
            "projects": [
                {
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "stale": False,
                    "heartbeat_overdue": True,
                    "seconds_until_expiry": filtered_body["project_claim_health_summary"]["projects"][0]["seconds_until_expiry"],
                }
            ],
            "total_projects": 1,
        }
        assert filtered_body["project_claim_health_summary"]["projects"][0]["seconds_until_expiry"] > 0
        assert filtered_body["worker_severity_summary"]["workers"][0]["severity_reasons"] == ["1 heartbeat-overdue claim"]
        assert filtered_body["project_severity_summary"]["projects"][0]["severity_reasons"] == ["heartbeat-overdue current claim"]
        assert filtered_body["worker_severity_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["1 heartbeat-overdue claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                }
            ],
            "total_workers": 1,
        }
        assert filtered_body["project_severity_summary"] == {
            "projects": [
                {
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["heartbeat-overdue current claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claim": False,
                    "heartbeat_overdue": True,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                }
            ],
            "total_projects": 1,
        }
        assert filtered_body["joined_severity_summary"] == {
            "items": [
                {
                    "scope": "project",
                    "project_id": overdue_project.project_id,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["heartbeat-overdue current claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "worker",
                    "project_id": None,
                    "worker_id": "worker_overdue",
                    "severity_score": 50,
                    "severity_reasons": ["1 heartbeat-overdue claim"],
                    "severity_contributions": [
                        {"signal": "heartbeat_overdue", "count": 1, "weight": 50, "contribution": 50}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 1,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
            ],
            "total_items": 2,
        }



def test_runtime_audits_allow_configurable_severity_weights() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    stale_project = store.create_project(Project(idea="A coroner sovereign keeps a kingdom alive through debt embalming."))
    noisy_project = store.create_project(Project(idea="A tribunal clerk forces nine border resets before breakfast."))

    now = utc_now()
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=stale_project.project_id,
            worker_id="worker_stale",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    for _ in range(9):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=noisy_project.project_id,
                actor_worker_id="worker_noisy",
                claim_worker_id="worker_foreign",
                forced=True,
                message="Forced release",
            )
        )

    with TestClient(app) as client:
        default_response = client.get("/api/runtime/audits")
        assert default_response.status_code == 200
        default_body = default_response.json()
        assert default_body["severity_weights"] == {
            "stale_claim_weight": 100,
            "heartbeat_overdue_weight": 50,
            "lease_loss_weight": 20,
            "forced_release_weight": 10,
            "stale_release_weight": 5,
            "recent_audit_weight": 1,
        }
        assert default_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 1,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }
        assert default_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 1,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }
        assert default_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 2,
            "recovery_noise_dominant": 2,
            "mixed_or_neutral": 0,
            "total_items": 4,
        }
        assert [worker["worker_id"] for worker in default_body["worker_severity_summary"]["workers"][:2]] == [
            "worker_stale",
            "worker_noisy",
        ]
        assert [worker["severity_score"] for worker in default_body["worker_severity_summary"]["workers"][:2]] == [100, 99]
        assert [project["project_id"] for project in default_body["project_severity_summary"]["projects"][:2]] == [
            stale_project.project_id,
            noisy_project.project_id,
        ]
        assert [project["severity_score"] for project in default_body["project_severity_summary"]["projects"][:2]] == [100, 99]

        weighted_response = client.get("/api/runtime/audits?stale_claim_weight=20&forced_release_weight=15")
        assert weighted_response.status_code == 200
        weighted_body = weighted_response.json()
        assert weighted_body["severity_weights"] == {
            "stale_claim_weight": 20,
            "heartbeat_overdue_weight": 50,
            "lease_loss_weight": 20,
            "forced_release_weight": 15,
            "stale_release_weight": 5,
            "recent_audit_weight": 1,
        }
        assert [worker["worker_id"] for worker in weighted_body["worker_severity_summary"]["workers"][:2]] == [
            "worker_noisy",
            "worker_stale",
        ]
        assert [worker["severity_score"] for worker in weighted_body["worker_severity_summary"]["workers"][:2]] == [144, 20]
        assert [project["project_id"] for project in weighted_body["project_severity_summary"]["projects"][:2]] == [
            noisy_project.project_id,
            stale_project.project_id,
        ]
        assert [project["severity_score"] for project in weighted_body["project_severity_summary"]["projects"][:2]] == [144, 20]



def test_runtime_severity_presets_are_discoverable() -> None:
    with TestClient(create_app(store=InMemoryStoryForgeStore())) as client:
        response = client.get("/api/runtime/severity-presets")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["presets"] == [
            {
                "name": "balanced",
                "category": "general",
                "labels": ["default", "balanced", "overview"],
                "window": "last_day",
                "severity_focus": None,
                "worker_severity_limit": 10,
                "project_severity_limit": 10,
                "worker_summary_limit": 10,
                "project_summary_limit": 10,
                "action_summary_limit": 10,
                "weights": {
                    "stale_claim_weight": 100,
                    "heartbeat_overdue_weight": 50,
                    "lease_loss_weight": 20,
                    "forced_release_weight": 10,
                    "stale_release_weight": 5,
                    "recent_audit_weight": 1,
                },
            },
            {
                "name": "claim_health",
                "category": "ownership",
                "labels": ["ownership", "claim-health", "triage"],
                "window": "last_week",
                "severity_focus": "claim_health",
                "worker_severity_limit": 10,
                "project_severity_limit": 10,
                "worker_summary_limit": 10,
                "project_summary_limit": 10,
                "action_summary_limit": 10,
                "weights": {
                    "stale_claim_weight": 150,
                    "heartbeat_overdue_weight": 100,
                    "lease_loss_weight": 10,
                    "forced_release_weight": 5,
                    "stale_release_weight": 5,
                    "recent_audit_weight": 0,
                },
            },
            {
                "name": "recovery_noise",
                "category": "recovery",
                "labels": ["recovery", "operator-churn", "triage"],
                "window": "last_hour",
                "severity_focus": "recovery_noise",
                "worker_severity_limit": 3,
                "project_severity_limit": 3,
                "worker_summary_limit": 3,
                "project_summary_limit": 3,
                "action_summary_limit": 3,
                "weights": {
                    "stale_claim_weight": 25,
                    "heartbeat_overdue_weight": 25,
                    "lease_loss_weight": 40,
                    "forced_release_weight": 20,
                    "stale_release_weight": 15,
                    "recent_audit_weight": 3,
                },
            },
        ]



def test_runtime_audits_support_severity_presets() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    stale_project = store.create_project(Project(idea="A bone census keeps a monarchy alive by taxing funerals."))
    noisy_project = store.create_project(Project(idea="A frontier clerk survives by force-resetting every failed handoff."))

    now = utc_now()
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=stale_project.project_id,
            worker_id="worker_stale",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    for _ in range(9):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=noisy_project.project_id,
                actor_worker_id="worker_noisy",
                claim_worker_id="worker_foreign",
                forced=True,
                created_at=now - timedelta(minutes=20),
                message="Forced release",
            )
        )
    for _ in range(2):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=noisy_project.project_id,
                actor_worker_id="worker_noisy",
                claim_worker_id="worker_foreign",
                forced=True,
                created_at=now - timedelta(days=2),
                message="Older forced release",
            )
        )

    with TestClient(app) as client:
        balanced_response = client.get("/api/runtime/audits?severity_preset=balanced")
        assert balanced_response.status_code == 200
        balanced_body = balanced_response.json()
        assert balanced_body["severity_preset"] == {
            "name": "balanced",
            "window": "last_day",
            "severity_focus": None,
            "worker_severity_limit": 10,
            "project_severity_limit": 10,
            "worker_summary_limit": 10,
            "project_summary_limit": 10,
            "action_summary_limit": 10,
        }
        assert balanced_body["filtered_total"] == 9
        assert balanced_body["severity_weights"] == {
            "stale_claim_weight": 100,
            "heartbeat_overdue_weight": 50,
            "lease_loss_weight": 20,
            "forced_release_weight": 10,
            "stale_release_weight": 5,
            "recent_audit_weight": 1,
        }
        assert balanced_body["worker_severity_summary"]["workers"][0]["worker_id"] == "worker_stale"
        assert balanced_body["project_severity_summary"]["projects"][0]["project_id"] == stale_project.project_id

        noise_response = client.get("/api/runtime/audits?severity_preset=recovery_noise")
        assert noise_response.status_code == 200
        noise_body = noise_response.json()
        assert noise_body["severity_preset"] == {
            "name": "recovery_noise",
            "window": "last_hour",
            "severity_focus": "recovery_noise",
            "worker_severity_limit": 3,
            "project_severity_limit": 3,
            "worker_summary_limit": 3,
            "project_summary_limit": 3,
            "action_summary_limit": 3,
        }
        assert noise_body["filtered_total"] == 9
        assert noise_body["severity_weights"] == {
            "stale_claim_weight": 25,
            "heartbeat_overdue_weight": 25,
            "lease_loss_weight": 40,
            "forced_release_weight": 20,
            "stale_release_weight": 15,
            "recent_audit_weight": 3,
        }
        assert noise_body["worker_severity_summary"]["workers"][0]["worker_id"] == "worker_noisy"
        assert noise_body["project_severity_summary"]["projects"][0]["project_id"] == noisy_project.project_id
        assert [worker["worker_id"] for worker in noise_body["worker_severity_summary"]["workers"]] == ["worker_noisy"]
        assert [project["project_id"] for project in noise_body["project_severity_summary"]["projects"]] == [
            noisy_project.project_id
        ]
        assert noise_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 1,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert noise_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 1,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert noise_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 2,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }

        override_focus_response = client.get("/api/runtime/audits?severity_preset=recovery_noise&severity_focus=claim_health")
        assert override_focus_response.status_code == 200
        override_focus_body = override_focus_response.json()
        assert [worker["worker_id"] for worker in override_focus_body["worker_severity_summary"]["workers"]] == [
            "worker_stale"
        ]
        assert [project["project_id"] for project in override_focus_body["project_severity_summary"]["projects"]] == [
            stale_project.project_id
        ]
        assert override_focus_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert override_focus_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert override_focus_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 2,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }

        override_response = client.get("/api/runtime/audits?severity_preset=recovery_noise&stale_claim_weight=400")
        assert override_response.status_code == 200
        override_body = override_response.json()
        assert override_body["severity_weights"]["stale_claim_weight"] == 400
        assert [worker["worker_id"] for worker in override_body["worker_severity_summary"]["workers"]] == [
            "worker_noisy"
        ]
        assert [project["project_id"] for project in override_body["project_severity_summary"]["projects"]] == [
            noisy_project.project_id
        ]

        invalid_response = client.get("/api/runtime/audits?severity_preset=unknown")
        assert invalid_response.status_code == 400
        assert invalid_response.json()["detail"] == "severity_preset must be one of: balanced, claim_health, recovery_noise"



def test_runtime_audits_support_contribution_aware_severity_focus() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    stale_project = store.create_project(Project(idea="A burial treasurer keeps a collapsing court alive through defaulted funerals."))
    noisy_project = store.create_project(Project(idea="A border magistrate survives by forcing repeated claim recoveries."))
    mixed_project = store.create_project(Project(idea="A salvage notary juggles stale ownership with constant operator churn."))

    now = utc_now()
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=stale_project.project_id,
            worker_id="worker_stale",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=mixed_project.project_id,
            worker_id="worker_mixed",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    for _ in range(4):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=noisy_project.project_id,
                actor_worker_id="worker_noisy",
                claim_worker_id="worker_foreign",
                forced=True,
                message="Forced release",
            )
        )
    for _ in range(12):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=mixed_project.project_id,
                actor_worker_id="worker_mixed",
                claim_worker_id="worker_foreign",
                forced=True,
                message="Mixed forced release",
            )
        )

    with TestClient(app) as client:
        claim_health_response = client.get("/api/runtime/audits?severity_focus=claim_health")
        assert claim_health_response.status_code == 200
        claim_health_body = claim_health_response.json()
        assert claim_health_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert claim_health_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 1,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 1,
        }
        assert claim_health_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 2,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }
        assert [worker["worker_id"] for worker in claim_health_body["worker_severity_summary"]["workers"]] == ["worker_stale"]
        assert [project["project_id"] for project in claim_health_body["project_severity_summary"]["projects"]] == [
            stale_project.project_id
        ]
        assert [item["scope"] for item in claim_health_body["joined_severity_summary"]["items"]] == ["project", "worker"]
        assert all(
            contribution["signal"] == "stale_claim"
            for item in claim_health_body["joined_severity_summary"]["items"]
            for contribution in item["severity_contributions"]
        )

        recovery_noise_response = client.get("/api/runtime/audits?severity_focus=recovery_noise")
        assert recovery_noise_response.status_code == 200
        recovery_noise_body = recovery_noise_response.json()
        assert recovery_noise_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 2,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }
        assert recovery_noise_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 2,
            "mixed_or_neutral": 0,
            "total_items": 2,
        }
        assert recovery_noise_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 4,
            "mixed_or_neutral": 0,
            "total_items": 4,
        }
        assert [worker["worker_id"] for worker in recovery_noise_body["worker_severity_summary"]["workers"]] == [
            "worker_mixed",
            "worker_noisy",
        ]
        assert [project["project_id"] for project in recovery_noise_body["project_severity_summary"]["projects"]] == [
            mixed_project.project_id,
            noisy_project.project_id,
        ]
        assert [item["project_id"] for item in recovery_noise_body["joined_severity_summary"]["items"] if item["scope"] == "project"] == [
            mixed_project.project_id,
            noisy_project.project_id,
        ]
        assert recovery_noise_body["worker_severity_summary"]["workers"][0]["severity_contributions"] == [
            {"signal": "forced_release", "count": 12, "weight": 10, "contribution": 120},
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100},
            {"signal": "recent_audit", "count": 12, "weight": 1, "contribution": 12},
        ]

        invalid_focus_response = client.get("/api/runtime/audits?severity_focus=unknown")
        assert invalid_focus_response.status_code == 400
        assert invalid_focus_response.json()["detail"] == "severity_focus must be one of: claim_health, recovery_noise"



def test_runtime_audits_join_worker_and_project_severity_rankings() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    stale_project = store.create_project(Project(idea="A plague registrar prices burial rights by district collapse."))
    noisy_project = store.create_project(Project(idea="A tribunal fixer survives by forcing constant control-plane recovery."))

    now = utc_now()
    store.save_project_claim(
        ProjectExecutionClaim(
            project_id=stale_project.project_id,
            worker_id="worker_stale",
            claimed_at=now - timedelta(seconds=60),
            lease_expires_at=now - timedelta(seconds=1),
            lease_duration_seconds=30.0,
            heartbeat_interval_seconds=10.0,
            last_heartbeat_at=now - timedelta(seconds=5),
        )
    )
    for _ in range(2):
        store.append_runtime_audit(
            RuntimeAuditRecord(
                action="release_project_claim",
                project_id=noisy_project.project_id,
                actor_worker_id="worker_noisy",
                claim_worker_id="worker_foreign",
                forced=True,
                message="Forced release",
            )
        )

    with TestClient(app) as client:
        response = client.get("/api/runtime/audits?joined_severity_limit=3")
        assert response.status_code == 200
        body = response.json()
        assert body["project_severity_summary"]["projects"][0]["severity_reasons"] == ["stale current claim"]
        assert body["project_severity_summary"]["projects"][0]["severity_contributions"] == [
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
        ]
        assert body["worker_severity_summary"]["workers"][0]["severity_reasons"] == ["1 stale claim"]
        assert body["worker_severity_summary"]["workers"][0]["severity_contributions"] == [
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
        ]
        assert body["joined_severity_summary"]["items"][0]["severity_contributions"] == [
            {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
        ]
        assert body["joined_severity_summary"] == {
            "items": [
                {
                    "scope": "project",
                    "project_id": stale_project.project_id,
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["stale current claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "worker",
                    "project_id": None,
                    "worker_id": "worker_stale",
                    "severity_score": 100,
                    "severity_reasons": ["1 stale claim"],
                    "severity_contributions": [
                        {"signal": "stale_claim", "count": 1, "weight": 100, "contribution": 100}
                    ],
                    "stale_claims": 1,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 1,
                    "recent_audit_count": 0,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                },
                {
                    "scope": "project",
                    "project_id": noisy_project.project_id,
                    "worker_id": None,
                    "severity_score": 22,
                    "severity_reasons": ["2 forced release audits"],
                    "severity_contributions": [
                        {"signal": "forced_release", "count": 2, "weight": 10, "contribution": 20},
                        {"signal": "recent_audit", "count": 2, "weight": 1, "contribution": 2}
                    ],
                    "stale_claims": 0,
                    "heartbeat_overdue_claims": 0,
                    "active_claims": 0,
                    "recent_audit_count": 2,
                    "lease_loss_count": 0,
                    "forced_release_count": 2,
                    "stale_release_count": 0,
                },
            ],
            "total_items": 4,
        }



def test_runtime_audits_support_filtering_and_pagination(tmp_path: Path) -> None:
    db_path = tmp_path / "audit-filters.db"

    store = SQLiteStoryForgeStore(str(db_path))
    app = create_app(store=store)

    with TestClient(app) as client:
        project_one = client.post(
            "/api/projects",
            json={"idea": "A mausoleum auditor builds statecraft from inheritance loopholes."},
        ).json()["project_id"]
        project_two = client.post(
            "/api/projects",
            json={"idea": "A census smuggler monetizes dynastic ghosts as labor futures."},
        ).json()["project_id"]

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_one,
                worker_id="worker_foreign",
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )
        force_release_response = client.delete(f"/api/projects/{project_one}/runtime/claim?force=true")
        assert force_release_response.status_code == 200

        expired_at = utc_now() - timedelta(seconds=5)
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_two,
                worker_id="worker_dead",
                claimed_at=expired_at,
                lease_expires_at=expired_at,
            )
        )
        stale_release_response = client.delete("/api/runtime/claims/stale")
        assert stale_release_response.status_code == 200

        all_response = client.get("/api/runtime/audits")
        assert all_response.status_code == 200
        all_body = all_response.json()
        assert all_body["total"] == 2
        assert all_body["filtered_total"] == 2
        assert all_body["offset"] == 0
        assert all_body["limit"] == 100
        assert all_body["lease_loss_summary"] == {
            "recent_count": 0,
            "affected_projects": 0,
            "latest_created_at": None,
            "latest_project_id": None,
        }
        assert all_body["claim_health_summary"] == {
            "total_claims": 0,
            "stale_claims": 0,
            "heartbeat_overdue_claims": 0,
            "healthy_claims": 0,
            "affected_projects": 0,
            "affected_workers": 0,
        }
        assert all_body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": store.list_runtime_audits()[0].actor_worker_id,
                    "total_count": 2,
                    "lease_loss_count": 0,
                    "forced_release_count": 1,
                    "stale_release_count": 1,
                }
            ],
            "total_workers": 1,
        }
        assert all_body["project_summary"]["total_projects"] == 2
        all_projects_by_id = {project["project_id"]: project for project in all_body["project_summary"]["projects"]}
        assert all_projects_by_id == {
            project_one: {
                "project_id": project_one,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 1,
                "stale_release_count": 0,
            },
            project_two: {
                "project_id": project_two,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 0,
                "stale_release_count": 1,
            },
        }

        action_response = client.get("/api/runtime/audits?action=release_project_claim")
        assert action_response.status_code == 200
        action_body = action_response.json()
        assert action_body["total"] == 2
        assert action_body["filtered_total"] == 1
        assert [record["project_id"] for record in action_body["records"]] == [project_one]

        stale_response = client.get("/api/runtime/audits?stale=true")
        assert stale_response.status_code == 200
        stale_body = stale_response.json()
        assert stale_body["filtered_total"] == 1
        assert [record["project_id"] for record in stale_body["records"]] == [project_two]

        actor_response = client.get("/api/runtime/audits?actor_worker_id=worker_foreign")
        assert actor_response.status_code == 200
        actor_body = actor_response.json()
        assert actor_body["filtered_total"] == 0
        assert actor_body["records"] == []
        assert actor_body["lease_loss_summary"]["recent_count"] == 0
        assert actor_body["claim_health_summary"] == {
            "total_claims": 0,
            "stale_claims": 0,
            "heartbeat_overdue_claims": 0,
            "healthy_claims": 0,
            "affected_projects": 0,
            "affected_workers": 0,
        }
        assert actor_body["worker_claim_health_summary"] == {"workers": [], "total_workers": 0}
        assert actor_body["project_claim_health_summary"] == {"projects": [], "total_projects": 0}
        assert actor_body["worker_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 0,
        }
        assert actor_body["project_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 0,
        }
        assert actor_body["joined_severity_focus_summary"] == {
            "claim_health_dominant": 0,
            "recovery_noise_dominant": 0,
            "mixed_or_neutral": 0,
            "total_items": 0,
        }
        assert actor_body["worker_severity_summary"] == {"workers": [], "total_workers": 0}
        assert actor_body["project_severity_summary"] == {"projects": [], "total_projects": 0}
        assert actor_body["worker_summary"] == {"workers": [], "total_workers": 0}
        assert actor_body["project_summary"] == {"projects": [], "total_projects": 0}
        assert actor_body["action_summary"] == {"actions": [], "total_actions": 0}

        forced_response = client.get("/api/runtime/audits?forced=true")
        assert forced_response.status_code == 200
        forced_body = forced_response.json()
        assert forced_body["filtered_total"] == 2

        paged_response = client.get("/api/runtime/audits?offset=1&limit=1")
        assert paged_response.status_code == 200
        paged_body = paged_response.json()
        assert paged_body["total"] == 2
        assert paged_body["filtered_total"] == 2
        assert paged_body["offset"] == 1
        assert paged_body["limit"] == 1
        assert len(paged_body["records"]) == 1

        project_response = client.get(f"/api/runtime/audits?project_id={project_two}")
        assert project_response.status_code == 200
        project_body = project_response.json()
        assert project_body["filtered_total"] == 1
        assert [record["project_id"] for record in project_body["records"]] == [project_two]



def test_runtime_audits_include_lease_loss_summary() -> None:
    store = InMemoryStoryForgeStore()
    workflow = ClosedLoopService(store, TaskStateMachine())
    runtime = WorkerRuntime(workflow)
    runtime._heartbeat_interval_seconds = 0.1
    app = create_app(store=store)

    with TestClient(app) as client:
        project_one = client.post(
            "/api/projects",
            json={"idea": "A treaty broker loses control to a faster census empire."},
        ).json()["project_id"]
        project_two = client.post(
            "/api/projects",
            json={"idea": "A relic appraiser drops a kingdom through missing signatures."},
        ).json()["project_id"]

        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_one,
                worker_id=runtime.worker_id,
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )
        heartbeat_one = runtime._start_claim_heartbeat(project_one)
        store.save_project_claim(
            ProjectExecutionClaim(
                project_id=project_two,
                worker_id=runtime.worker_id,
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
            )
        )
        heartbeat_two = runtime._start_claim_heartbeat(project_two)

        try:
            store.delete_project_claim(project_one)
            store.save_project_claim(
                ProjectExecutionClaim(
                    project_id=project_two,
                    worker_id="worker_foreign",
                    claimed_at=utc_now(),
                    lease_expires_at=utc_now() + timedelta(seconds=30),
                )
            )

            deadline = time.time() + 1.0
            while time.time() < deadline:
                records = store.list_runtime_audits()
                if len(records) >= 2:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Lease-loss audit summary setup did not finish in time")
        finally:
            heartbeat_one.set()
            heartbeat_two.set()

        all_response = client.get("/api/runtime/audits")
        assert all_response.status_code == 200
        all_body = all_response.json()
        summary = all_body["lease_loss_summary"]
        assert summary["recent_count"] == 2
        assert summary["affected_projects"] == 2
        assert summary["latest_created_at"] is not None
        assert summary["latest_project_id"] in {project_one, project_two}
        worker_summary = all_body["worker_summary"]
        assert worker_summary == {
            "workers": [
                {
                    "worker_id": runtime.worker_id,
                    "total_count": 2,
                    "lease_loss_count": 2,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                }
            ],
            "total_workers": 1,
        }

        filtered_response = client.get(f"/api/runtime/audits?project_id={project_two}&action=claim_heartbeat_lost")
        assert filtered_response.status_code == 200
        filtered_body = filtered_response.json()
        filtered_summary = filtered_body["lease_loss_summary"]
        assert filtered_body["filtered_total"] == 1
        assert filtered_summary["recent_count"] == 1
        assert filtered_summary["affected_projects"] == 1
        assert filtered_summary["latest_project_id"] == project_two
        assert filtered_body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": runtime.worker_id,
                    "total_count": 1,
                    "lease_loss_count": 1,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                }
            ],
            "total_workers": 1,
        }
        assert filtered_body["project_summary"] == {
            "projects": [
                {
                    "project_id": project_two,
                    "total_count": 1,
                    "lease_loss_count": 1,
                    "forced_release_count": 0,
                    "stale_release_count": 0,
                }
            ],
            "total_projects": 1,
        }
        assert filtered_body["action_summary"] == {
            "actions": [
                {
                    "action": "claim_heartbeat_lost",
                    "total_count": 1,
                    "forced_count": 0,
                    "stale_count": 0,
                }
            ],
            "total_actions": 1,
        }



def test_runtime_audits_include_worker_summary_across_workers() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    project_one = store.create_project(Project(idea="A customs broker misfiles empires into default."))
    project_two = store.create_project(Project(idea="A grave notary rewrites inheritance through attrition."))
    project_three = store.create_project(Project(idea="A tax pilgrim liquidates miracles as bond inventory."))

    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="claim_heartbeat_lost",
            project_id=project_one.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_foreign",
            message="Ownership moved",
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_project_claim",
            project_id=project_two.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_foreign",
            forced=True,
            message="Forced release",
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_stale_project_claim",
            project_id=project_three.project_id,
            actor_worker_id="worker_beta",
            claim_worker_id="worker_dead",
            forced=True,
            stale=True,
            message="Released stale claim",
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/audits")
        assert response.status_code == 200
        body = response.json()
        assert body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_alpha",
                    "total_count": 2,
                    "lease_loss_count": 1,
                    "forced_release_count": 1,
                    "stale_release_count": 0,
                },
                {
                    "worker_id": "worker_beta",
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                },
            ],
            "total_workers": 2,
        }
        assert body["project_summary"]["total_projects"] == 3
        projects_by_id = {project["project_id"]: project for project in body["project_summary"]["projects"]}
        assert projects_by_id == {
            project_one.project_id: {
                "project_id": project_one.project_id,
                "total_count": 1,
                "lease_loss_count": 1,
                "forced_release_count": 0,
                "stale_release_count": 0,
            },
            project_two.project_id: {
                "project_id": project_two.project_id,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 1,
                "stale_release_count": 0,
            },
            project_three.project_id: {
                "project_id": project_three.project_id,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 0,
                "stale_release_count": 1,
            },
        }
        assert body["action_summary"] == {
            "actions": [
                {
                    "action": "claim_heartbeat_lost",
                    "total_count": 1,
                    "forced_count": 0,
                    "stale_count": 0,
                },
                {
                    "action": "release_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 0,
                },
                {
                    "action": "release_stale_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 1,
                },
            ],
            "total_actions": 3,
        }

        filtered = client.get("/api/runtime/audits?actor_worker_id=worker_beta")
        assert filtered.status_code == 200
        filtered_body = filtered.json()
        assert filtered_body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_beta",
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                }
            ],
            "total_workers": 1,
        }
        assert filtered_body["project_summary"] == {
            "projects": [
                {
                    "project_id": project_three.project_id,
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                }
            ],
            "total_projects": 1,
        }
        assert filtered_body["action_summary"] == {
            "actions": [
                {
                    "action": "release_stale_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 1,
                }
            ],
            "total_actions": 1,
        }



def test_runtime_audits_support_time_window_filters() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    project_old = store.create_project(Project(idea="A weather broker buries dynasties in forecast debt."))
    project_mid = store.create_project(Project(idea="A salt registrar mints succession from missing ledgers."))
    project_new = store.create_project(Project(idea="A shrine accountant prices rebellions as temple arrears."))

    oldest_time = utc_now() - timedelta(hours=3)
    middle_time = utc_now() - timedelta(hours=2)
    newest_time = utc_now() - timedelta(hours=1)

    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="claim_heartbeat_lost",
            project_id=project_old.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_old",
            message="Old lease loss",
            created_at=oldest_time,
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_project_claim",
            project_id=project_mid.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_mid",
            forced=True,
            message="Middle forced release",
            created_at=middle_time,
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_stale_project_claim",
            project_id=project_new.project_id,
            actor_worker_id="worker_beta",
            claim_worker_id="worker_new",
            forced=True,
            stale=True,
            message="Newest stale release",
            created_at=newest_time,
        )
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/runtime/audits?created_after={middle_time.isoformat()}&created_before={newest_time.isoformat()}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["filtered_total"] == 2
        assert [record["project_id"] for record in body["records"]] == [project_mid.project_id, project_new.project_id]
        assert body["lease_loss_summary"] == {
            "recent_count": 0,
            "affected_projects": 0,
            "latest_created_at": None,
            "latest_project_id": None,
        }
        assert body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_alpha",
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 1,
                    "stale_release_count": 0,
                },
                {
                    "worker_id": "worker_beta",
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                },
            ],
            "total_workers": 2,
        }
        assert body["project_summary"]["total_projects"] == 2
        projects_by_id = {project["project_id"]: project for project in body["project_summary"]["projects"]}
        assert projects_by_id == {
            project_mid.project_id: {
                "project_id": project_mid.project_id,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 1,
                "stale_release_count": 0,
            },
            project_new.project_id: {
                "project_id": project_new.project_id,
                "total_count": 1,
                "lease_loss_count": 0,
                "forced_release_count": 0,
                "stale_release_count": 1,
            },
        }
        assert body["action_summary"] == {
            "actions": [
                {
                    "action": "release_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 0,
                },
                {
                    "action": "release_stale_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 1,
                },
            ],
            "total_actions": 2,
        }

        lower_bound_only = client.get(f"/api/runtime/audits?created_after={newest_time.isoformat()}")
        assert lower_bound_only.status_code == 200
        lower_body = lower_bound_only.json()
        assert lower_body["filtered_total"] == 1
        assert [record["project_id"] for record in lower_body["records"]] == [project_new.project_id]
        assert lower_body["project_summary"] == {
            "projects": [
                {
                    "project_id": project_new.project_id,
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                }
            ],
            "total_projects": 1,
        }



def test_runtime_audits_reject_invalid_time_window_filters() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        invalid_after = client.get("/api/runtime/audits?created_after=not-a-date")
        assert invalid_after.status_code == 400
        assert invalid_after.json()["detail"] == "created_after must be a valid ISO 8601 datetime"

        invalid_before = client.get("/api/runtime/audits?created_before=still-not-a-date")
        assert invalid_before.status_code == 400
        assert invalid_before.json()["detail"] == "created_before must be a valid ISO 8601 datetime"

        reversed_window = client.get(
            "/api/runtime/audits?created_after=2026-04-20T12:00:00&created_before=2026-04-20T11:00:00"
        )
        assert reversed_window.status_code == 400
        assert reversed_window.json()["detail"] == "created_after must be less than or equal to created_before"



def test_runtime_audits_can_limit_summary_lengths_without_trimming_records() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    project_one = store.create_project(Project(idea="A canal broker arbitrages coups through toll defaults."))
    project_two = store.create_project(Project(idea="A siege notary inventories monarchies as spoilage."))
    project_three = store.create_project(Project(idea="A relic banker prices afterlives as sovereign debt."))

    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="claim_heartbeat_lost",
            project_id=project_one.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_foreign",
            message="Ownership moved",
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_project_claim",
            project_id=project_two.project_id,
            actor_worker_id="worker_alpha",
            claim_worker_id="worker_foreign",
            forced=True,
            message="Forced release",
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_stale_project_claim",
            project_id=project_three.project_id,
            actor_worker_id="worker_beta",
            claim_worker_id="worker_dead",
            forced=True,
            stale=True,
            message="Released stale claim",
        )
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/runtime/audits?worker_summary_limit=1&project_summary_limit=2&action_summary_limit=2"
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["records"]) == 3
        assert body["filtered_total"] == 3
        assert body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_alpha",
                    "total_count": 2,
                    "lease_loss_count": 1,
                    "forced_release_count": 1,
                    "stale_release_count": 0,
                }
            ],
            "total_workers": 2,
        }
        assert body["project_summary"]["total_projects"] == 3
        assert len(body["project_summary"]["projects"]) == 2
        project_ids = [project["project_id"] for project in body["project_summary"]["projects"]]
        assert set(project_ids).issubset({project_one.project_id, project_two.project_id, project_three.project_id})
        assert body["action_summary"] == {
            "actions": [
                {
                    "action": "claim_heartbeat_lost",
                    "total_count": 1,
                    "forced_count": 0,
                    "stale_count": 0,
                },
                {
                    "action": "release_project_claim",
                    "total_count": 1,
                    "forced_count": 1,
                    "stale_count": 0,
                },
            ],
            "total_actions": 3,
        }



def test_runtime_audits_support_relative_windows() -> None:
    store = InMemoryStoryForgeStore()
    app = create_app(store=store)

    project_old = store.create_project(Project(idea="A relic broker sells dead empires as salvage futures."))
    project_recent = store.create_project(Project(idea="A tariff monk securitizes border sin into treasury notes."))
    project_latest = store.create_project(Project(idea="A shrine clerk invoices miracles as municipal debt."))

    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="claim_heartbeat_lost",
            project_id=project_old.project_id,
            actor_worker_id="worker_old",
            claim_worker_id="worker_foreign",
            message="Old record",
            created_at=utc_now() - timedelta(days=2),
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_project_claim",
            project_id=project_recent.project_id,
            actor_worker_id="worker_recent",
            claim_worker_id="worker_foreign",
            forced=True,
            message="Recent record",
            created_at=utc_now() - timedelta(hours=6),
        )
    )
    store.append_runtime_audit(
        RuntimeAuditRecord(
            action="release_stale_project_claim",
            project_id=project_latest.project_id,
            actor_worker_id="worker_latest",
            claim_worker_id="worker_dead",
            forced=True,
            stale=True,
            message="Latest record",
            created_at=utc_now() - timedelta(minutes=20),
        )
    )

    with TestClient(app) as client:
        last_day = client.get("/api/runtime/audits?window=last_day")
        assert last_day.status_code == 200
        last_day_body = last_day.json()
        assert last_day_body["filtered_total"] == 2
        assert [record["project_id"] for record in last_day_body["records"]] == [project_recent.project_id, project_latest.project_id]
        assert last_day_body["project_summary"]["total_projects"] == 2
        assert last_day_body["action_summary"]["total_actions"] == 2

        last_hour = client.get("/api/runtime/audits?window=last_hour")
        assert last_hour.status_code == 200
        last_hour_body = last_hour.json()
        assert last_hour_body["filtered_total"] == 1
        assert [record["project_id"] for record in last_hour_body["records"]] == [project_latest.project_id]
        assert last_hour_body["worker_summary"] == {
            "workers": [
                {
                    "worker_id": "worker_latest",
                    "total_count": 1,
                    "lease_loss_count": 0,
                    "forced_release_count": 0,
                    "stale_release_count": 1,
                }
            ],
            "total_workers": 1,
        }



def test_runtime_audits_reject_invalid_relative_windows() -> None:
    app = create_app(store=InMemoryStoryForgeStore())

    with TestClient(app) as client:
        invalid_window = client.get("/api/runtime/audits?window=last_month")
        assert invalid_window.status_code == 400
        assert invalid_window.json()["detail"] == "window must be one of: last_hour, last_day, last_week"

        conflicting_window = client.get("/api/runtime/audits?window=last_day&created_after=2026-04-20T10:00:00")
        assert conflicting_window.status_code == 400
        assert conflicting_window.json()["detail"] == "window cannot be combined with created_after or created_before"
