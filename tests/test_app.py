import json

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyforge.api.app import create_app, TokenAuth
from storyforge.execution.store import InMemoryStoryForgeStore


def test_health() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "storyforge"}



def test_frontend_index_serves_workbench() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>StoryForge</title>" in response.text
    assert "StoryForge Workbench" in response.text
    assert "生产控制台" in response.text
    assert "production-branch" in response.text
    assert "retry-failed-btn" in response.text
    assert "cancel-pending-btn" in response.text
    assert "audit-panel-content" in response.text
    assert "quality_details" in response.text
    assert "rule_audits" in response.text
    assert "rule-form" in response.text
    assert "quality-experiments-card" in response.text
    assert "quality-experiments-list" in response.text
    assert "renderQualityExperiments" in response.text
    assert "/quality-experiments?${branchQuery}" in response.text
    assert "approval_rate" in response.text
    assert 'background: var(--bg)' in response.text
    assert 'background: #000' not in response.text
    assert 'background: #000000' not in response.text
    assert "throw new Error(`${path}:" in response.text
    assert "catch (error) { setFlash(error.message || \"请求失败。\", \"error\"); }" in response.text
    assert "emptyProductionConsole(projectId)" in response.text
    assert "/production-console?${branchQuery}`).catch" in response.text


def test_frontend_bootstrap_waits_for_full_dom() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/")

    assert response.status_code == 200
    assert 'window.addEventListener("DOMContentLoaded", bootstrap);' in response.text
    assert "\n      bootstrap();" not in response.text


def test_frontend_index_returns_503_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import import_module
    import importlib.util
    import pathlib
    # Load the raw module file to bypass __init__.py re-export
    module_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "storyforge" / "api" / "app.py"
    spec = importlib.util.spec_from_file_location("_raw_app_module", module_path)
    raw_module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["_raw_app_module"] = raw_module
    spec.loader.exec_module(raw_module)
    missing_path = tmp_path / "nonexistent_index.html"
    monkeypatch.setattr(raw_module, "FRONTEND_INDEX_PATH", missing_path)
    client = TestClient(raw_module.create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/")

    assert response.status_code == 503
    assert "Workbench frontend not available" in response.json()["detail"]


def test_project_and_task_flow() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A fallen cultivator rebuilds his path through forbidden machine scripture."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    task_response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={"task_type": "brief_generation", "payload": {"mode": "initial"}},
    )
    assert task_response.status_code == 200
    task = task_response.json()
    task_id = task["task_id"]
    assert task["status"] == "queued"

    start_response = client.post(
        f"/api/tasks/{task_id}/start",
        json={"step": "brief_generation", "progress": 0.2},
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "running"

    complete_response = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"step": "brief_generation", "progress": 1.0},
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    events_response = client.get(f"/api/tasks/{task_id}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()]
    assert event_types == ["accepted", "queued", "started", "completed"]


def test_first_closed_loop_creates_assets_and_completes_tasks() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={
            "idea": "An exiled heir builds a hidden city from broken relic machines.",
            "genre": "xuanhuan",
            "target_length": 24,
        },
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    queue_response = client.post(
        f"/api/projects/{project_id}/runs/first-loop",
        json={"chapter_number": 1},
    )
    assert queue_response.status_code == 200
    queued_tasks = queue_response.json()
    assert [task["task_type"] for task in queued_tasks] == [
        "brief_generation",
        "outline_generation",
        "chapter_generation",
        "chapter_review",
    ]
    assert all(task["status"] == "queued" for task in queued_tasks)

    drain_response = client.post(f"/api/projects/{project_id}/workers/drain")
    assert drain_response.status_code == 200
    assert drain_response.json()["processed_count"] == 4

    tasks_response = client.get(f"/api/projects/{project_id}/tasks")
    assert tasks_response.status_code == 200
    assert all(task["status"] == "completed" for task in tasks_response.json())

    assets_response = client.get(f"/api/projects/{project_id}/assets")
    assert assets_response.status_code == 200
    assert [asset["asset_type"] for asset in assets_response.json()] == [
        "brief",
        "outline",
        "chapter",
        "review_note",
    ]

    chapter_response = client.get(f"/api/projects/{project_id}/assets/chapter")
    assert chapter_response.status_code == 200
    chapter_asset = chapter_response.json()
    assert chapter_asset["structured_data"]["chapter_number"] == 1

    project_state = client.get(f"/api/projects/{project_id}")
    assert project_state.status_code == 200
    assert project_state.json()["latest_chapter_cursor"] == 1
    assert project_state.json()["current_phase"] == "review"


def test_create_asset_manually() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "An author builds a story bible by hand before drafting."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "characters",
            "content": "Lin Yue: cautious inventor with a hidden grudge.",
            "source": "human",
            "structured_data": {"lead": "Lin Yue"},
        },
    )
    assert create_asset_response.status_code == 200
    asset = create_asset_response.json()
    assert asset["project_id"] == project_id
    assert asset["asset_type"] == "characters"
    assert asset["source"] == "human"
    assert asset["version"] == 1
    assert asset["structured_data"]["lead"] == "Lin Yue"

    assets_response = client.get(f"/api/projects/{project_id}/assets")
    assert assets_response.status_code == 200
    assert len(assets_response.json()) == 1
    assert assets_response.json()[0]["asset_id"] == asset["asset_id"]



def test_edit_asset_creates_new_version() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A strategist refines worldbuilding notes during production."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "world",
            "content": "The empire runs on relic engines.",
            "structured_data": {"region": "North"},
        },
    )
    assert create_asset_response.status_code == 200
    original_asset = create_asset_response.json()

    update_asset_response = client.put(
        f"/api/projects/{project_id}/assets/{original_asset['asset_id']}",
        json={
            "content": "The empire runs on relic engines and oath-bound guilds.",
            "structured_data": {"region": "North", "guilds": 7},
        },
    )
    assert update_asset_response.status_code == 200
    updated_asset = update_asset_response.json()

    assert updated_asset["asset_id"] != original_asset["asset_id"]
    assert updated_asset["project_id"] == project_id
    assert updated_asset["asset_type"] == "world"
    assert updated_asset["source"] == "human"
    assert updated_asset["version"] == 2
    assert updated_asset["structured_data"]["guilds"] == 7

    latest_asset_response = client.get(f"/api/projects/{project_id}/assets/world")
    assert latest_asset_response.status_code == 200
    assert latest_asset_response.json()["asset_id"] == updated_asset["asset_id"]

    assets_response = client.get(f"/api/projects/{project_id}/assets")
    assert assets_response.status_code == 200
    assert [asset["version"] for asset in assets_response.json()] == [1, 2]



def test_edit_asset_preserves_existing_structured_metadata_when_omitted() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "An editor revises prose without re-entering all chapter metadata."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "chapter",
            "content": "Original chapter body.",
            "structured_data": {"chapter_number": 1, "title": "Arrival", "status": "draft"},
        },
    )
    assert create_asset_response.status_code == 200
    original_asset = create_asset_response.json()

    update_asset_response = client.put(
        f"/api/projects/{project_id}/assets/{original_asset['asset_id']}",
        json={
            "content": "Revised chapter body.",
        },
    )
    assert update_asset_response.status_code == 200
    updated_asset = update_asset_response.json()

    assert updated_asset["structured_data"]["chapter_number"] == 1
    assert updated_asset["structured_data"]["title"] == "Arrival"
    assert updated_asset["structured_data"]["status"] == "draft"



def test_list_asset_versions_returns_all_versions_sorted() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "A versioned asset test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(f"/api/projects/{project_id}/assets", json={"asset_type": "rules", "content": "Rule one."})
    client.put(f"/api/projects/{project_id}/assets/{client.get(f'/api/projects/{project_id}/assets/rules').json()['asset_id']}", json={"content": "Rule one revised."})
    client.post(f"/api/projects/{project_id}/assets", json={"asset_type": "rules", "content": "Rule two."})

    versions_response = client.get(f"/api/projects/{project_id}/assets/rules/versions")
    assert versions_response.status_code == 200
    versions = versions_response.json()
    assert len(versions) == 3
    assert [v["version"] for v in versions] == [1, 2, 3]


def test_diff_asset_versions_returns_unified_diff() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "A diff test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={"asset_type": "rules", "content": "Line one\nLine two\nLine three\n"})
    asset_id = create_resp.json()["asset_id"]
    client.put(f"/api/projects/{project_id}/assets/{asset_id}", json={"content": "Line one\nLine two modified\nLine three\nLine four\n"})

    diff_response = client.post(f"/api/projects/{project_id}/assets/rules/diff")
    assert diff_response.status_code == 200
    diff_text = diff_response.text
    assert "--- v1" in diff_text
    assert "+++ v2" in diff_text
    assert "-Line two" in diff_text
    assert "+Line two modified" in diff_text
    assert "+Line four" in diff_text



def test_update_project_settings() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "A strategist refines settings at runtime."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    patch_response = client.patch(
        f"/api/projects/{project_id}",
        json={"title": "Relic City", "genre": "xuanhuan", "target_length": 24},
    )
    assert patch_response.status_code == 200
    updated = patch_response.json()
    assert updated["title"] == "Relic City"
    assert updated["genre"] == "xuanhuan"
    assert updated["target_length"] == 24
    assert updated["idea"] == "A strategist refines settings at runtime."

    get_response = client.get(f"/api/projects/{project_id}")
    assert get_response.status_code == 200
    assert get_response.json()["title"] == "Relic City"



def test_project_collaborators_can_be_added_and_listed() -> None:
    auth = TokenAuth()
    auth.register_token("editor-token", "editor-lin")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    project_response = client.post("/api/projects", json={"idea": "A small studio co-writes a serial."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    add_response = client.post(
        f"/api/projects/{project_id}/collaborators",
        json={"actor_id": "editor-lin", "actor_name": "Lin", "role": "editor"},
    )
    assert add_response.status_code == 200
    project = add_response.json()
    collaborators = project["collaborators"]
    assert len(collaborators) == 1
    assert collaborators[0]["actor_id"] == "editor-lin"
    assert collaborators[0]["actor_name"] == "Lin"
    assert collaborators[0]["role"] == "editor"

    list_response = client.get(f"/api/projects/{project_id}/collaborators", headers={"Authorization": "Bearer editor-token"})
    assert list_response.status_code == 200
    assert list_response.json() == collaborators



def test_edit_asset_rejects_stale_base_version() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "Two editors race on the same chapter notes."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "world",
            "content": "The empire runs on relic engines.",
            "source": "human",
            "structured_data": {"region": "North"},
        },
    )
    assert create_asset_response.status_code == 200
    original_asset = create_asset_response.json()

    first_save_response = client.put(
        f"/api/projects/{project_id}/assets/{original_asset['asset_id']}",
        json={
            "content": "The empire runs on relic engines and oath-bound guilds.",
            "source": "human",
            "base_version": 1,
            "actor_id": "writer-yan",
            "actor_name": "Yan",
            "change_note": "expand setting",
            "structured_data": {"region": "North", "guilds": 7},
        },
    )
    assert first_save_response.status_code == 200
    updated_asset = first_save_response.json()
    assert updated_asset["structured_data"]["actor_id"] == "writer-yan"
    assert updated_asset["structured_data"]["actor_name"] == "Yan"
    assert updated_asset["structured_data"]["change_note"] == "expand setting"

    stale_save_response = client.put(
        f"/api/projects/{project_id}/assets/{original_asset['asset_id']}",
        json={
            "content": "A conflicting edit.",
            "source": "human",
            "base_version": 1,
            "actor_id": "editor-lin",
            "actor_name": "Lin",
            "change_note": "conflicting pass",
            "structured_data": {"region": "North", "guilds": 9},
        },
    )
    assert stale_save_response.status_code == 409
    assert stale_save_response.json()["detail"] == "A newer asset version already exists"



def test_edit_asset_rejects_cross_project_access() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_one = client.post("/api/projects", json={"idea": "first"}).json()
    project_two = client.post("/api/projects", json={"idea": "second"}).json()

    create_asset_response = client.post(
        f"/api/projects/{project_one['project_id']}/assets",
        json={"asset_type": "rules", "content": "No resurrection.", "source": "human"},
    )
    assert create_asset_response.status_code == 200
    asset_id = create_asset_response.json()["asset_id"]

    update_asset_response = client.put(
        f"/api/projects/{project_two['project_id']}/assets/{asset_id}",
        json={"content": "Actually, resurrection is rare."},
    )
    assert update_asset_response.status_code == 404



def test_edit_chapter_allows_update_when_other_chapter_has_newer_version() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Two chapter streams should not block each other."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    chapter_one = client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Chapter one v1", "structured_data": {"chapter_number": 1, "title": "Arrival"}},
    ).json()
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Chapter two v1", "structured_data": {"chapter_number": 2, "title": "Debt"}},
    )

    update_response = client.put(
        f"/api/projects/{project_id}/assets/{chapter_one['asset_id']}",
        json={
            "content": "Chapter one v2",
            "base_version": 1,
            "structured_data": {"chapter_number": 1, "title": "Arrival"},
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["content"] == "Chapter one v2"
    assert update_response.json()["structured_data"]["chapter_number"] == 1



def test_export_project_as_markdown_uses_latest_chapter_versions() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A survivor compiles a finished manuscript from revised chapters.", "title": "Relic City"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Old chapter one", "structured_data": {"chapter_number": 1, "title": "Arrival"}},
    )
    client.put(
        f"/api/projects/{project_id}/assets/{client.get(f'/api/projects/{project_id}/assets/chapter').json()['asset_id']}",
        json={"content": "Revised chapter one", "structured_data": {"chapter_number": 1, "title": "Arrival"}},
    )
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Chapter two body", "structured_data": {"chapter_number": 2, "title": "Debt"}},
    )

    export_response = client.post(f"/api/projects/{project_id}/export", json={})

    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/markdown")
    body = export_response.text
    assert "# Relic City" in body
    assert "## Chapter 1: Arrival" in body
    assert "Revised chapter one" in body
    assert "Old chapter one" not in body
    assert "## Chapter 2: Debt" in body



def test_export_project_as_text() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A scribe exports plain text chapters.", "title": "Plain Export"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Text body", "structured_data": {"chapter_number": 1, "title": "Opening"}},
    )

    export_response = client.post(
        f"/api/projects/{project_id}/export",
        json={"format": "text"},
    )

    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/plain")
    assert "Plain Export" in export_response.text
    assert "Chapter 1: Opening" in export_response.text
    assert "Text body" in export_response.text



def test_export_project_as_qidian_text() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A hero rises from the gutter.", "title": "Sky Breaker"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Qidian body", "structured_data": {"chapter_number": 1, "title": "Awakening"}},
    )

    export_response = client.post(
        f"/api/projects/{project_id}/export",
        json={"format": "qidian"},
    )

    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/plain")
    assert export_response.headers["content-disposition"].endswith('"Sky_Breaker_qidian.txt"')
    assert "《Sky Breaker》" in export_response.text
    assert "第1章 Awakening" in export_response.text
    assert "Qidian body" in export_response.text



def test_export_project_as_jinjiang_text() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A court romance blooms in secret.", "title": "Moon Courtyard"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Jinjiang body", "structured_data": {"chapter_number": 1, "title": "First Snow"}},
    )

    export_response = client.post(
        f"/api/projects/{project_id}/export",
        json={"format": "jinjiang"},
    )

    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/plain")
    assert export_response.headers["content-disposition"].endswith('"Moon_Courtyard_jinjiang.txt"')
    assert "【作品名】Moon Courtyard" in export_response.text
    assert "【章节 1】First Snow" in export_response.text
    assert "Jinjiang body" in export_response.text



def test_export_sanitizes_download_filename() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "header safety", "title": 'Bad "Title\r\nX-Test: 1'},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Safe body", "structured_data": {"chapter_number": 1, "title": "Opening"}},
    )

    export_response = client.post(f"/api/projects/{project_id}/export", json={})

    assert export_response.status_code == 200
    disposition = export_response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert 'filename="Bad_Title_X-Test_1.md"' in disposition



def test_export_rejects_invalid_format() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "invalid format", "title": "Bad Format"})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    export_response = client.post(
        f"/api/projects/{project_id}/export",
        json={"format": "pdf"},
    )

    assert export_response.status_code == 422



def test_export_skips_invalid_chapter_number_metadata() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "bad metadata", "title": "Safe Export"})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Broken string chapter", "structured_data": {"chapter_number": "one", "title": "Broken String"}},
    )
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Broken bool chapter", "structured_data": {"chapter_number": True, "title": "Broken Bool"}},
    )
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Broken float chapter", "structured_data": {"chapter_number": 1.9, "title": "Broken Float"}},
    )
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Broken unicode chapter", "structured_data": {"chapter_number": "²", "title": "Broken Unicode"}},
    )
    client.post(
        f"/api/projects/{project_id}/assets",
        json={"asset_type": "chapter", "content": "Good chapter", "structured_data": {"chapter_number": 2, "title": "Valid"}},
    )

    export_response = client.post(f"/api/projects/{project_id}/export", json={})

    assert export_response.status_code == 200
    assert "Good chapter" in export_response.text
    assert "Broken string chapter" not in export_response.text
    assert "Broken bool chapter" not in export_response.text
    assert "Broken float chapter" not in export_response.text
    assert "Broken unicode chapter" not in export_response.text



def test_project_summary_includes_llm_usage() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A scribe tracks token costs."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    queue_response = client.post(
        f"/api/projects/{project_id}/runs/first-loop",
        json={"chapter_number": 1},
    )
    assert queue_response.status_code == 200

    client.post(f"/api/projects/{project_id}/workers/drain")

    summary_response = client.get(f"/api/projects/{project_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()

    assert "llm_usage" in summary
    usage = summary["llm_usage"]
    assert "input_tokens" in usage
    assert "output_tokens" in usage
    assert "total_tokens" in usage
    # In skip-llm mode, all counts are 0, but the structure is present
    assert isinstance(usage["input_tokens"], int)
    assert isinstance(usage["output_tokens"], int)
    assert isinstance(usage["total_tokens"], int)


def test_project_summary_includes_quality_experiment_metrics() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A producer compares review variants."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    task_response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={
            "task_type": "chapter_review",
            "payload": {"chapter_number": 1},
            "config": {
                "values": {
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "strict",
                    "llm_usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
                }
            },
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["task_id"]

    start_response = client.post(
        f"/api/tasks/{task_id}/start",
        json={"step": "chapter_review", "progress": 0.5},
    )
    assert start_response.status_code == 200
    complete_response = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"step": "chapter_review.completed", "progress": 1.0},
    )
    assert complete_response.status_code == 200
    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "review_note",
            "content": "review",
            "source": "system",
            "structured_data": {
                "chapter_number": 1,
                "approved": False,
                "issues": ["pacing", "clarity"],
            },
        },
    )
    assert create_asset_response.status_code == 200

    summary_response = client.get(f"/api/projects/{project_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()

    assert "quality_experiments" in summary
    experiments = summary["quality_experiments"]
    assert len(experiments) == 1
    experiment = experiments[0]
    assert experiment["experiment"] == "review-ab-v1"
    assert experiment["variant"] == "strict"
    assert experiment["review_count"] == 1
    assert experiment["approved_count"] == 0
    assert experiment["rewrite_count"] == 1
    assert experiment["issue_count"] == 2
    assert experiment["input_tokens"] == 120
    assert experiment["output_tokens"] == 80
    assert experiment["total_tokens"] == 200



def test_project_summary_ignores_malformed_quality_llm_usage() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A producer compares review variants."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    task_response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={
            "task_type": "chapter_review",
            "payload": {"chapter_number": 1},
            "config": {
                "values": {
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "lenient",
                    "llm_usage": {"input_tokens": "n/a", "output_tokens": None, "total_tokens": ""},
                }
            },
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["task_id"]

    start_response = client.post(
        f"/api/tasks/{task_id}/start",
        json={"step": "chapter_review", "progress": 0.5},
    )
    assert start_response.status_code == 200
    complete_response = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"step": "chapter_review.completed", "progress": 1.0},
    )
    assert complete_response.status_code == 200

    summary_response = client.get(f"/api/projects/{project_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()

    experiments = summary["quality_experiments"]
    assert len(experiments) == 1
    experiment = experiments[0]
    assert experiment["experiment"] == "review-ab-v1"
    assert experiment["variant"] == "lenient"
    assert experiment["input_tokens"] == 0
    assert experiment["output_tokens"] == 0
    assert experiment["total_tokens"] == 0



def test_quality_experiments_api_is_branch_scoped_and_reports_rates() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="quality experiment branches", branches=["main", "alt"]))
    client = TestClient(create_app(store=store))

    main_task = store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            effective_config_snapshot=ConfigSnapshot(
                values={
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "strict",
                    "llm_usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
                }
            ),
        )
    )
    alt_task = store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="alt",
            payload={"chapter_number": 1},
            effective_config_snapshot=ConfigSnapshot(
                values={
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "strict",
                    "llm_usage": {"input_tokens": 900, "output_tokens": 90, "total_tokens": 990},
                }
            ),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="main review",
            structured_data={
                "chapter_number": 1,
                "quality_experiment": "review-ab-v1",
                "quality_variant": "strict",
                "approved": False,
                "issues": ["pacing", "clarity"],
                "failed_checks": ["quality:pacing", "rule:no-info-dump", "quality:pacing"],
            },
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="alt",
            content="alt review",
            structured_data={
                "chapter_number": 1,
                "quality_experiment": "review-ab-v1",
                "quality_variant": "strict",
                "approved": True,
                "issues": [],
                "failed_checks": [],
            },
        )
    )

    main_response = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=main")
    alt_response = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=alt")

    assert main_response.status_code == 200
    assert alt_response.status_code == 200
    main_body = main_response.json()
    alt_body = alt_response.json()
    assert main_body["project_id"] == project.project_id
    assert main_body["branch"] == "main"
    assert len(main_body["experiments"]) == 1
    main_experiment = main_body["experiments"][0]
    assert main_experiment["experiment"] == "review-ab-v1"
    assert main_experiment["variant"] == "strict"
    assert main_experiment["review_count"] == 1
    assert main_experiment["approved_count"] == 0
    assert main_experiment["rewrite_count"] == 1
    assert main_experiment["issue_count"] == 2
    assert main_experiment["approval_rate"] == 0.0
    assert main_experiment["rewrite_rate"] == 1.0
    assert main_experiment["average_issue_count"] == 2.0
    assert main_experiment["failed_check_distribution"] == {"quality:pacing": 2, "rule:no-info-dump": 1}
    assert main_experiment["input_tokens"] == 100
    assert main_experiment["output_tokens"] == 40
    assert main_experiment["total_tokens"] == 140
    assert alt_body["experiments"][0]["approval_rate"] == 1.0
    assert alt_body["experiments"][0]["total_tokens"] == 990
    assert main_task.branch == "main"
    assert alt_task.branch == "alt"

    missing_branch = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=missing")
    invalid_branch = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=bad branch/name")
    assert missing_branch.status_code == 404
    assert invalid_branch.status_code == 400



def test_quality_experiments_api_links_review_assets_by_output_ref_before_chapter_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="quality experiment attribution", branches=["main"]))
    strict_review = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="strict review",
            structured_data={"chapter_number": 1, "approved": False, "issues": ["strict issue"]},
        )
    )
    lenient_review = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="lenient review",
            structured_data={"chapter_number": 1, "approved": True, "issues": []},
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            output_refs=[strict_review.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"}),
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            output_refs=[lenient_review.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "lenient"}),
        )
    )
    client = TestClient(create_app(store=store))

    response = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=main")

    assert response.status_code == 200
    experiments = {item["variant"]: item for item in response.json()["experiments"]}
    assert experiments["strict"]["approved_count"] == 0
    assert experiments["strict"]["rewrite_count"] == 1
    assert experiments["strict"]["issue_count"] == 1
    assert experiments["lenient"]["approved_count"] == 1
    assert experiments["lenient"]["rewrite_count"] == 0
    assert experiments["lenient"]["issue_count"] == 0



def test_quality_experiments_api_skips_ambiguous_chapter_ref_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="quality experiment ambiguous chapter refs", branches=["main"]))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="chapter",
            structured_data={"chapter_number": 1},
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            input_asset_refs=[chapter.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"}),
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            input_asset_refs=[chapter.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "lenient"}),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="ambiguous review",
            structured_data={"chapter_number": 1, "chapter_ref": chapter.asset_id, "approved": False, "issues": ["ambiguous"]},
        )
    )
    client = TestClient(create_app(store=store))

    response = client.get(f"/api/projects/{project.project_id}/quality-experiments?branch=main")

    assert response.status_code == 200
    experiments = {item["variant"]: item for item in response.json()["experiments"]}
    assert experiments["strict"]["review_count"] == 1
    assert experiments["lenient"]["review_count"] == 1
    assert experiments["strict"]["rewrite_count"] == 0
    assert experiments["lenient"]["rewrite_count"] == 0
    assert experiments["strict"]["issue_count"] == 0
    assert experiments["lenient"]["issue_count"] == 0



def test_project_summary_links_reviews_by_output_ref_and_skips_ambiguous_chapter_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="summary quality attribution", branches=["main"]))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="chapter",
            structured_data={"chapter_number": 1},
        )
    )
    strict_review = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="strict review",
            structured_data={"chapter_number": 1, "approved": False, "issues": ["strict issue"]},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="ambiguous review",
            structured_data={"chapter_number": 1, "chapter_ref": chapter.asset_id, "approved": True, "issues": []},
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            input_asset_refs=[chapter.asset_id],
            output_refs=[strict_review.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"}),
        )
    )
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            input_asset_refs=[chapter.asset_id],
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "lenient"}),
        )
    )
    client = TestClient(create_app(store=store))

    response = client.get(f"/api/projects/{project.project_id}/summary")

    assert response.status_code == 200
    experiments = {item["variant"]: item for item in response.json()["quality_experiments"]}
    assert experiments["strict"]["approved_count"] == 0
    assert experiments["strict"]["rewrite_count"] == 1
    assert experiments["strict"]["issue_count"] == 1
    assert experiments["lenient"]["approved_count"] == 0
    assert experiments["lenient"]["rewrite_count"] == 0
    assert experiments["lenient"]["issue_count"] == 0



def test_project_summary_skips_deleted_review_notes_in_quality_metrics() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="deleted quality review", branches=["main"]))
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": 1},
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"}),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="deleted review",
            structured_data={
                "chapter_number": 1,
                "quality_experiment": "review-ab-v1",
                "quality_variant": "strict",
                "approved": False,
                "issues": ["deleted issue"],
            },
            is_deleted=True,
        )
    )
    client = TestClient(create_app(store=store))

    response = client.get(f"/api/projects/{project.project_id}/summary")

    assert response.status_code == 200
    experiment = response.json()["quality_experiments"][0]
    assert experiment["review_count"] == 1
    assert experiment["rewrite_count"] == 0
    assert experiment["issue_count"] == 0



def test_project_summary_uses_string_chapter_numbers_for_unambiguous_quality_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, ConfigSnapshot, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="string chapter quality attribution", branches=["main"]))
    store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="main",
            payload={"chapter_number": "1"},
            effective_config_snapshot=ConfigSnapshot(values={"quality_experiment": "review-ab-v1", "quality_variant": "strict"}),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="string chapter review",
            structured_data={"chapter_number": "1", "approved": False, "issues": ["string fallback"]},
        )
    )
    client = TestClient(create_app(store=store))

    response = client.get(f"/api/projects/{project.project_id}/summary")

    assert response.status_code == 200
    experiment = response.json()["quality_experiments"][0]
    assert experiment["review_count"] == 1
    assert experiment["rewrite_count"] == 1
    assert experiment["issue_count"] == 1



def test_project_summary_excludes_non_review_task_usage_from_quality_metrics() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={"idea": "A producer compares review variants."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    chapter_task_response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={
            "task_type": "chapter_generation",
            "payload": {"chapter_number": 1},
            "config": {
                "values": {
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "strict",
                    "llm_usage": {"input_tokens": 999, "output_tokens": 111, "total_tokens": 1110},
                }
            },
        },
    )
    assert chapter_task_response.status_code == 200

    review_task_response = client.post(
        f"/api/projects/{project_id}/tasks",
        json={
            "task_type": "chapter_review",
            "payload": {"chapter_number": 1},
            "config": {
                "values": {
                    "quality_experiment": "review-ab-v1",
                    "quality_variant": "strict",
                    "llm_usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
                }
            },
        },
    )
    assert review_task_response.status_code == 200
    task_id = review_task_response.json()["task_id"]

    start_response = client.post(
        f"/api/tasks/{task_id}/start",
        json={"step": "chapter_review", "progress": 0.5},
    )
    assert start_response.status_code == 200
    complete_response = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"step": "chapter_review.completed", "progress": 1.0},
    )
    assert complete_response.status_code == 200

    create_asset_response = client.post(
        f"/api/projects/{project_id}/assets",
        json={
            "asset_type": "review_note",
            "content": "review",
            "source": "system",
            "structured_data": {
                "chapter_number": 1,
                "approved": True,
                "issues": [],
                "quality_experiment": "review-ab-v1",
                "quality_variant": "strict",
            },
        },
    )
    assert create_asset_response.status_code == 200

    summary_response = client.get(f"/api/projects/{project_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()

    experiments = summary["quality_experiments"]
    assert len(experiments) == 1
    experiment = experiments[0]
    assert experiment["input_tokens"] == 120
    assert experiment["output_tokens"] == 80
    assert experiment["total_tokens"] == 200



def test_first_loop_outline_has_arc_structure() -> None:
    """Verify that outline assets include arcs array and arc_number on chapters."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post(
        "/api/projects",
        json={
            "idea": "An exiled heir builds a hidden city.",
            "genre": "xuanhuan",
            "target_length": 24,
        },
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(f"/api/projects/{project_id}/runs/first-loop", json={"chapter_number": 1})
    client.post(f"/api/projects/{project_id}/workers/drain")

    outline = client.get(f"/api/projects/{project_id}/assets/outline").json()
    chapters = outline["structured_data"]["chapters"]
    arcs = outline["structured_data"].get("arcs", [])

    # Flat chapters should all have arc_number
    assert len(chapters) == 3
    for ch in chapters:
        assert "arc_number" in ch
        assert ch["arc_number"] == 1

    # Arcs array should exist with at least one arc
    assert len(arcs) >= 1
    assert arcs[0]["arc_number"] == 1
    assert "title" in arcs[0]
    assert "chapters" in arcs[0]


def test_sqlite_store_persists_across_app_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "storyforge.db"

    first_client = TestClient(create_app(db_path=str(db_path)))
    project_response = first_client.post(
        "/api/projects",
        json={"idea": "A machine-bound prophet rebuilds a dynasty from the ruins."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    queue_response = first_client.post(
        f"/api/projects/{project_id}/runs/first-loop",
        json={"chapter_number": 1},
    )
    assert queue_response.status_code == 200
    first_client.post(f"/api/projects/{project_id}/workers/drain")
    first_client.close()

    second_client = TestClient(create_app(db_path=str(db_path)))

    projects_response = second_client.get("/api/projects")
    assert projects_response.status_code == 200
    assert len(projects_response.json()) == 1

    tasks_response = second_client.get(f"/api/projects/{project_id}/tasks")
    assert tasks_response.status_code == 200
    assert len(tasks_response.json()) == 4
    assert all(task["status"] == "completed" for task in tasks_response.json())

    assets_response = second_client.get(f"/api/projects/{project_id}/assets")
    assert assets_response.status_code == 200
    assert [asset["asset_type"] for asset in assets_response.json()] == [
        "brief",
        "outline",
        "chapter",
        "review_note",
    ]

    events_response = second_client.get(f"/api/tasks/{tasks_response.json()[0]['task_id']}/events")
    assert events_response.status_code == 200
    assert [event["event_type"] for event in events_response.json()] == ["accepted", "queued", "started", "completed"]


def test_sse_events_endpoint_returns_streaming() -> None:
    """Verify the SSE events endpoint returns text/event-stream content type."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "SSE events test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    # Create a task to generate at least one event
    client.post(f"/api/projects/{project_id}/tasks", json={
        "task_type": "brief_generation",
    })

    # SSE endpoint with max_events=1 should return quickly
    response = client.get(f"/api/projects/{project_id}/events?max_events=1")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


def test_project_events_can_filter_backlog_by_branch() -> None:
    from storyforge.domain.models import EventRecord, EventType, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch event stream", branches=["main", "alt"]))
    main_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.chapter_generation, branch="main"))
    alt_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.chapter_generation, branch="alt"))
    store.append_event(EventRecord(project_id=project.project_id, task_id=alt_task.task_id, event_type=EventType.queued, step="queued", message="alt event", payload={"branch": "alt"}))
    store.append_event(EventRecord(project_id=project.project_id, task_id=main_task.task_id, event_type=EventType.queued, step="queued", message="main event", payload={"branch": "main"}))

    response = client.get(f"/api/projects/{project.project_id}/events?branch=main&max_events=1")

    assert response.status_code == 200
    assert "main event" in response.text
    assert "alt event" not in response.text


def test_collaborator_project_writes_require_authorized_non_viewer_token() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="asset token permissions",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="original", structured_data={"chapter_number": 1}))

    create_impersonated = client.post(f"/api/projects/{project.project_id}/assets?actor_id=editor-eva", json={"asset_type": "world", "content": "impersonated"})
    create_viewer = client.post(f"/api/projects/{project.project_id}/assets", headers={"Authorization": "Bearer viewer-token"}, json={"asset_type": "world", "content": "viewer"})
    create_editor = client.post(f"/api/projects/{project.project_id}/assets", headers={"Authorization": "Bearer editor-token"}, json={"asset_type": "world", "content": "editor"})
    edit_impersonated = client.put(f"/api/projects/{project.project_id}/assets/{asset.asset_id}?actor_id=editor-eva", json={"content": "impersonated edit"})
    edit_viewer = client.put(f"/api/projects/{project.project_id}/assets/{asset.asset_id}", headers={"Authorization": "Bearer viewer-token"}, json={"content": "viewer edit"})
    edit_editor = client.put(f"/api/projects/{project.project_id}/assets/{asset.asset_id}", headers={"Authorization": "Bearer editor-token"}, json={"content": "editor edit"})
    delete_viewer = client.delete(f"/api/projects/{project.project_id}/assets/{asset.asset_id}", headers={"Authorization": "Bearer viewer-token"})
    delete_editor = client.delete(f"/api/projects/{project.project_id}/assets/{asset.asset_id}", headers={"Authorization": "Bearer editor-token"})
    add_collaborator_impersonated = client.post(
        f"/api/projects/{project.project_id}/collaborators?actor_id=author",
        json={"actor_id": "new-editor", "actor_name": "New", "role": "editor"},
    )

    assert create_impersonated.status_code == 403
    assert create_viewer.status_code == 403
    assert create_editor.status_code == 200
    assert edit_impersonated.status_code == 403
    assert edit_viewer.status_code == 403
    assert edit_editor.status_code == 200
    assert delete_viewer.status_code == 403
    assert delete_editor.status_code == 200
    assert add_collaborator_impersonated.status_code == 403


def test_task_and_worker_paths_require_authorized_non_viewer_token() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project, TaskRecord, TaskStatus, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="task token permissions",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="one paragraph\n\nanother paragraph",
            structured_data={"chapter_number": 1},
        )
    )
    task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main", status=TaskStatus.queued))
    failed_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main", status=TaskStatus.failed))
    running_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main", status=TaskStatus.running))

    unauthorized_responses = [
        client.post(f"/api/projects/{project.project_id}/tasks", json={"task_type": "brief_generation", "branch": "main"}),
        client.post(f"/api/projects/{project.project_id}/runs/first-loop", json={"branch": "main"}),
        client.post(f"/api/projects/{project.project_id}/runs/next-chapter", json={"branch": "main"}),
        client.post(f"/api/projects/{project.project_id}/runs/chapter-loop", json={"branch": "main"}),
        client.post(f"/api/projects/{project.project_id}/spot-fix", json={"chapter_asset_id": chapter.asset_id, "paragraph_indices": [0], "fix_instruction": "tighten"}),
        client.post(f"/api/projects/{project.project_id}/runtime/enqueue"),
        client.post(f"/api/projects/{project.project_id}/runtime/process-now?branch=main"),
        client.post(f"/api/projects/{project.project_id}/workers/process-next?branch=main"),
        client.post(f"/api/projects/{project.project_id}/workers/drain?branch=main"),
        client.get(f"/api/tasks/{task.task_id}"),
        client.get(f"/api/tasks/{task.task_id}/events"),
        client.post(f"/api/tasks/{task.task_id}/cancel"),
        client.post(f"/api/tasks/{failed_task.task_id}/retry"),
        client.post(f"/api/tasks/{task.task_id}/start", json={"step": "manual", "progress": 0.1}),
        client.post(f"/api/tasks/{running_task.task_id}/complete", json={"step": "manual", "progress": 1.0}),
    ]
    viewer_responses = [
        client.post(f"/api/projects/{project.project_id}/tasks", headers={"Authorization": "Bearer viewer-token"}, json={"task_type": "brief_generation", "branch": "main"}),
        client.post(f"/api/tasks/{task.task_id}/cancel", headers={"Authorization": "Bearer viewer-token"}),
    ]
    authorized_responses = [
        client.get(f"/api/tasks/{task.task_id}", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/tasks/{task.task_id}/events", headers={"Authorization": "Bearer editor-token"}),
        client.post(f"/api/projects/{project.project_id}/tasks", headers={"Authorization": "Bearer editor-token"}, json={"task_type": "brief_generation", "branch": "main"}),
        client.post(f"/api/projects/{project.project_id}/spot-fix", headers={"Authorization": "Bearer editor-token"}, json={"chapter_asset_id": chapter.asset_id, "paragraph_indices": [0], "fix_instruction": "tighten"}),
        client.post(f"/api/projects/{project.project_id}/runtime/enqueue", headers={"Authorization": "Bearer editor-token"}),
        client.post(f"/api/tasks/{task.task_id}/cancel", headers={"Authorization": "Bearer editor-token"}),
    ]

    assert [response.status_code for response in unauthorized_responses] == [403] * len(unauthorized_responses)
    assert [response.status_code for response in viewer_responses] == [403] * len(viewer_responses)
    assert [response.status_code for response in authorized_responses] == [200] * len(authorized_responses)


def test_asset_comments_require_authorized_read_write_tokens() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="comment token permissions",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, content="brief", comments=[{"author": "old", "content": "note"}]))

    list_without_token = client.get(f"/api/projects/{project.project_id}/assets/{asset.asset_id}/comments")
    add_without_token = client.post(f"/api/projects/{project.project_id}/assets/{asset.asset_id}/comments", json={"author": "Anon", "content": "no"})
    list_with_viewer = client.get(f"/api/projects/{project.project_id}/assets/{asset.asset_id}/comments", headers={"Authorization": "Bearer viewer-token"})
    add_with_viewer = client.post(f"/api/projects/{project.project_id}/assets/{asset.asset_id}/comments", headers={"Authorization": "Bearer viewer-token"}, json={"author": "Viewer", "content": "no"})
    add_with_editor = client.post(f"/api/projects/{project.project_id}/assets/{asset.asset_id}/comments", headers={"Authorization": "Bearer editor-token"}, json={"author": "Editor", "content": "yes"})

    assert list_without_token.status_code == 403
    assert add_without_token.status_code == 403
    assert list_with_viewer.status_code == 200
    assert add_with_viewer.status_code == 403
    assert add_with_editor.status_code == 200


def test_viewer_cannot_edit_asset() -> None:
    """Verify that a viewer role receives 403 when attempting to edit an asset."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Role-based access control test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "World setting.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    client.post(f"/api/projects/{project_id}/collaborators", json={
        "actor_id": "viewer-amy", "actor_name": "Amy", "role": "viewer",
    })

    # Viewer tries to edit
    edit_resp = client.put(
        f"/api/projects/{project_id}/assets/{asset_id}?actor_id=viewer-amy",
        json={"content": "Viewer tries to edit."},
    )
    assert edit_resp.status_code == 403


def test_viewer_cannot_create_asset() -> None:
    """Verify that a viewer role receives 403 when attempting to create an asset."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Viewer cannot create assets."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(f"/api/projects/{project_id}/collaborators", json={
        "actor_id": "viewer-bob", "actor_name": "Bob", "role": "viewer",
    })

    create_resp = client.post(
        f"/api/projects/{project_id}/assets?actor_id=viewer-bob",
        json={"asset_type": "world", "content": "Viewer tries to create."},
    )
    assert create_resp.status_code == 403


def test_viewer_cannot_manage_collaborators() -> None:
    """Verify that a viewer role receives 403 when attempting to add collaborators."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Viewers cannot manage collaborators."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    client.post(f"/api/projects/{project_id}/collaborators", json={
        "actor_id": "viewer-carol", "actor_name": "Carol", "role": "viewer",
    })

    add_resp = client.post(
        f"/api/projects/{project_id}/collaborators?actor_id=viewer-carol",
        json={"actor_id": "viewer-dave", "actor_name": "Dave", "role": "viewer"},
    )
    assert add_resp.status_code == 403


def test_editor_can_edit_assets() -> None:
    """Verify that an editor role can edit assets."""
    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    project_response = client.post("/api/projects", json={"idea": "Editor edit test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "Original.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    client.post(f"/api/projects/{project_id}/collaborators", json={
        "actor_id": "editor-eva", "actor_name": "Eva", "role": "editor",
    })

    edit_resp = client.put(
        f"/api/projects/{project_id}/assets/{asset_id}",
        headers={"Authorization": "Bearer editor-token"},
        json={"content": "Edited by editor.", "actor_id": "editor-eva", "actor_name": "Eva"},
    )
    assert edit_resp.status_code == 200


def test_author_can_manage_collaborators() -> None:
    """Verify that the project owner (no collaborators listed = author) can manage collaborators."""
    auth = TokenAuth()
    auth.register_token("owner-token", "owner-1")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    project_response = client.post(
        "/api/projects",
        headers={"Authorization": "Bearer owner-token"},
        json={"idea": "Author manages collaborators."},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    add_resp = client.post(
        f"/api/projects/{project_id}/collaborators",
        headers={"Authorization": "Bearer owner-token"},
        json={"actor_id": "editor-fred", "actor_name": "Fred", "role": "editor"},
    )
    assert add_resp.status_code == 200


def test_unlisted_token_cannot_manage_collaborators_on_owned_project() -> None:
    from storyforge.domain.models import CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("owner-token", "owner-1")
    auth.register_token("intruder-token", "intruder")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(Project(idea="owned", owner_id="owner-1"))
    ownerless_project = store.create_project(
        Project(
            idea="legacy collaborator-only protected",
            collaborators=[CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor")],
        )
    )

    intruder = client.post(
        f"/api/projects/{project.project_id}/collaborators",
        headers={"Authorization": "Bearer intruder-token"},
        json={"actor_id": "new-editor", "actor_name": "New", "role": "editor"},
    )
    legacy_intruder = client.post(
        f"/api/projects/{ownerless_project.project_id}/collaborators",
        headers={"Authorization": "Bearer intruder-token"},
        json={"actor_id": "new-editor", "actor_name": "New", "role": "editor"},
    )
    owner = client.post(
        f"/api/projects/{project.project_id}/collaborators",
        headers={"Authorization": "Bearer owner-token"},
        json={"actor_id": "real-editor", "actor_name": "Real", "role": "editor"},
    )

    assert intruder.status_code == 403
    assert legacy_intruder.status_code == 403
    assert owner.status_code == 200


def test_owned_project_read_surfaces_require_authorized_tokens() -> None:
    from storyforge.domain.models import CollaboratorRecord, EventRecord, EventType, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("owner-token", "owner-1")
    auth.register_token("viewer-token", "viewer-amy")
    auth.register_token("intruder-token", "intruder")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    public_project = store.create_project(Project(idea="public"))
    owned_project = store.create_project(
        Project(
            idea="owned read surfaces",
            owner_id="owner-1",
            collaborators=[CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer")],
        )
    )
    task = store.create_task(TaskRecord(project_id=owned_project.project_id, task_type=TaskType.brief_generation, status="queued"))
    store.append_event(EventRecord(project_id=owned_project.project_id, task_id=task.task_id, event_type=EventType.queued, step="queued"))

    unauthorized = [
        client.get(f"/api/projects/{owned_project.project_id}"),
        client.get(f"/api/projects/{owned_project.project_id}/collaborators"),
        client.get(f"/api/projects/{owned_project.project_id}/summary"),
        client.get(f"/api/projects/{owned_project.project_id}/events?max_events=1"),
    ]
    intruder = client.get(f"/api/projects/{owned_project.project_id}", headers={"Authorization": "Bearer intruder-token"})
    owner = client.get(f"/api/projects/{owned_project.project_id}", headers={"Authorization": "Bearer owner-token"})
    viewer_collaborators = client.get(f"/api/projects/{owned_project.project_id}/collaborators", headers={"Authorization": "Bearer viewer-token"})
    viewer_summary = client.get(f"/api/projects/{owned_project.project_id}/summary", headers={"Authorization": "Bearer viewer-token"})
    viewer_events = client.get(f"/api/projects/{owned_project.project_id}/events?max_events=1", headers={"Authorization": "Bearer viewer-token"})
    project_list_unauthorized = client.get("/api/projects")
    project_list_viewer = client.get("/api/projects", headers={"Authorization": "Bearer viewer-token"})

    assert [response.status_code for response in unauthorized] == [403, 403, 403, 403]
    assert intruder.status_code == 403
    assert owner.status_code == 200
    assert viewer_collaborators.status_code == 200
    assert viewer_summary.status_code == 200
    assert viewer_events.status_code == 200
    assert [project["project_id"] for project in project_list_unauthorized.json()] == [public_project.project_id]
    assert {project["project_id"] for project in project_list_viewer.json()} == {public_project.project_id, owned_project.project_id}


def test_owned_runtime_global_lists_do_not_leak_inaccessible_counts() -> None:
    from datetime import timedelta
    from storyforge.domain.models import Project, ProjectExecutionClaim, RuntimeAuditRecord, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("owner-one-token", "owner-1")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    public_project = store.create_project(Project(idea="public runtime"))
    visible_project = store.create_project(Project(idea="visible runtime", owner_id="owner-1"))
    hidden_project = store.create_project(Project(idea="hidden runtime", owner_id="owner-2"))
    expired = utc_now() - timedelta(seconds=30)
    active = utc_now() + timedelta(seconds=30)
    store.save_project_claim(ProjectExecutionClaim(project_id=public_project.project_id, worker_id="worker-public", lease_expires_at=active))
    store.save_project_claim(ProjectExecutionClaim(project_id=visible_project.project_id, worker_id="worker-visible", lease_expires_at=active))
    store.save_project_claim(ProjectExecutionClaim(project_id=hidden_project.project_id, worker_id="worker-hidden", lease_expires_at=expired))
    store.append_runtime_audit(RuntimeAuditRecord(action="claim_acquired", project_id=public_project.project_id, actor_worker_id="worker-public"))
    store.append_runtime_audit(RuntimeAuditRecord(action="claim_acquired", project_id=visible_project.project_id, actor_worker_id="worker-visible"))
    store.append_runtime_audit(RuntimeAuditRecord(action="claim_acquired", project_id=hidden_project.project_id, actor_worker_id="worker-hidden"))

    claims = client.get("/api/runtime/claims", headers={"Authorization": "Bearer owner-one-token"})
    scoped_claims = client.get(
        f"/api/runtime/claims?project_id={visible_project.project_id}",
        headers={"Authorization": "Bearer owner-one-token"},
    )
    audits = client.get("/api/runtime/audits", headers={"Authorization": "Bearer owner-one-token"})

    assert claims.status_code == 200
    claim_body = claims.json()
    assert claim_body["total"] == 2
    assert claim_body["filtered_total"] == 2
    assert claim_body["stale_count"] == 0
    assert {claim["project_id"] for claim in claim_body["claims"]} == {public_project.project_id, visible_project.project_id}
    assert scoped_claims.status_code == 200
    scoped_claim_body = scoped_claims.json()
    assert scoped_claim_body["total"] == 1
    assert scoped_claim_body["filtered_total"] == 1
    assert scoped_claim_body["stale_count"] == 0
    assert [claim["project_id"] for claim in scoped_claim_body["claims"]] == [visible_project.project_id]
    assert audits.status_code == 200
    audit_body = audits.json()
    assert audit_body["total"] == 2
    assert audit_body["filtered_total"] == 2
    assert {record["project_id"] for record in audit_body["records"]} == {public_project.project_id, visible_project.project_id}


def test_rollback_asset_version() -> None:
    """Verify that rolling back creates a new version from target version's content."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Rollback test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    # Create v1
    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "Version 1 content",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]
    assert create_resp.json()["version"] == 1

    # Edit to create v2
    edit_resp = client.put(
        f"/api/projects/{project_id}/assets/{asset_id}",
        json={"content": "Version 2 content", "base_version": 1},
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["version"] == 2

    # Rollback to v1 -> creates v3
    rollback_resp = client.post(
        f"/api/projects/{project_id}/assets/{asset_id}/rollback",
        json={"target_version": 1},
    )
    assert rollback_resp.status_code == 200
    rolled_back = rollback_resp.json()
    assert rolled_back["version"] == 3
    assert rolled_back["content"] == "Version 1 content"
    assert rolled_back["structured_data"]["rollback_from_version"] == 1


def test_rollback_requires_target_version() -> None:
    """Verify that rollback returns 400 when target_version is missing."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Rollback validation test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "Initial.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    resp = client.post(f"/api/projects/{project_id}/assets/{asset_id}/rollback", json={})
    assert resp.status_code == 400


def test_rollback_nonexistent_version_returns_404() -> None:
    """Verify that rolling back to a nonexistent version returns 404."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Rollback 404 test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "Initial.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    resp = client.post(
        f"/api/projects/{project_id}/assets/{asset_id}/rollback",
        json={"target_version": 999},
    )
    assert resp.status_code == 404


def test_soft_delete_asset() -> None:
    """Verify that deleting an asset hides it from list but keeps it in trash."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Delete test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "To be deleted.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    # Verify asset appears in list
    assets_before = client.get(f"/api/projects/{project_id}/assets").json()
    assert any(a["asset_id"] == asset_id for a in assets_before)

    # Delete it
    delete_resp = client.delete(f"/api/projects/{project_id}/assets/{asset_id}")
    assert delete_resp.status_code == 200

    # Verify asset no longer appears in list
    assets_after = client.get(f"/api/projects/{project_id}/assets").json()
    assert not any(a["asset_id"] == asset_id for a in assets_after)

    # Verify asset appears in trash
    trash = client.get(f"/api/projects/{project_id}/assets/trash").json()
    assert any(t["asset_id"] == asset_id for t in trash)


def test_restore_deleted_asset() -> None:
    """Verify that restoring a deleted asset brings it back to the active list."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Restore test."})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    create_resp = client.post(f"/api/projects/{project_id}/assets", json={
        "asset_type": "world", "content": "Deleted then restored.",
    })
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["asset_id"]

    # Delete
    client.delete(f"/api/projects/{project_id}/assets/{asset_id}")

    # Restore
    restore_resp = client.post(f"/api/projects/{project_id}/assets/{asset_id}/restore")
    assert restore_resp.status_code == 200
    restored = restore_resp.json()
    assert restored["asset_id"] == asset_id
    assert restored["is_deleted"] is False

    # Verify back in active list
    assets = client.get(f"/api/projects/{project_id}/assets").json()
    assert any(a["asset_id"] == asset_id for a in assets)

    # Trash should be empty
    trash = client.get(f"/api/projects/{project_id}/assets/trash").json()
    assert len(trash) == 0


def test_rate_limit_returns_429_after_exceeding() -> None:
    """Verify that exceeding the rate limit returns 429."""
    from storyforge.api.app import RateLimiter

    limiter = RateLimiter(max_requests=3, window_seconds=60)

    # First 3 requests should be allowed
    for i in range(3):
        allowed, remaining = limiter.is_allowed("test-client")
        assert allowed is True
        assert remaining == 2 - i

    # 4th request should be denied
    allowed, remaining = limiter.is_allowed("test-client")
    assert allowed is False
    assert remaining == 0


def test_rate_limit_resets_after_window() -> None:
    """Verify that rate limit resets after the window expires."""
    import time
    from storyforge.api.app import RateLimiter

    limiter = RateLimiter(max_requests=2, window_seconds=1)

    limiter.is_allowed("client-a")
    limiter.is_allowed("client-a")

    # Should be denied now
    allowed, _ = limiter.is_allowed("client-a")
    assert allowed is False

    # Wait for window to expire
    time.sleep(1.1)

    allowed, remaining = limiter.is_allowed("client-a")
    assert allowed is True
    # Window expired, fresh quota: 2 max, 1 used → remaining=1
    assert remaining == 1


def test_rate_limit_per_client_isolation() -> None:
    """Verify that rate limits are tracked per client."""
    from storyforge.api.app import RateLimiter

    limiter = RateLimiter(max_requests=2, window_seconds=60)

    # Client A uses up its quota
    limiter.is_allowed("client-a")
    limiter.is_allowed("client-a")

    # Client B should still have quota
    allowed, remaining = limiter.is_allowed("client-b")
    assert allowed is True
    assert remaining == 1


def test_auth_generate_and_verify_token() -> None:
    """Verify token generation and verification flow."""
    auth = TokenAuth()
    auth.register_token("bootstrap-token", "bootstrap-admin")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    # Generate token
    resp = client.post("/api/auth/token", headers={"Authorization": "Bearer bootstrap-token"}, json={"user_id": "bootstrap-admin"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert token.startswith("sf_")
    assert resp.json()["user_id"] == "bootstrap-admin"

    # Verify token
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["user_id"] == "bootstrap-admin"


def test_auth_missing_header_returns_401() -> None:
    """Verify that /api/auth/me returns 401 without Authorization header."""
    auth = TokenAuth()
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_auth_token_generation_requires_existing_valid_token() -> None:
    auth = TokenAuth()
    auth.register_token("bootstrap-token", "bootstrap-admin")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    missing = client.post("/api/auth/token", json={"user_id": "owner-1"})
    invalid = client.post("/api/auth/token", headers={"Authorization": "Bearer invalid"}, json={"user_id": "owner-1"})
    impersonation = client.post("/api/auth/token", headers={"Authorization": "Bearer bootstrap-token"}, json={"user_id": "owner-1"})
    authorized = client.post("/api/auth/token", headers={"Authorization": "Bearer bootstrap-token"}, json={"user_id": "bootstrap-admin"})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert impersonation.status_code == 403
    assert authorized.status_code == 200


def test_auth_invalid_token_returns_401() -> None:
    """Verify that /api/auth/me returns 401 with invalid token."""
    auth = TokenAuth()
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid_token"})
    assert resp.status_code == 401


def test_auth_revoke_token() -> None:
    """Verify that revoking a token makes it invalid."""
    auth = TokenAuth()
    auth.register_token("bootstrap-token", "bootstrap-admin")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    # Generate
    resp = client.post("/api/auth/token", headers={"Authorization": "Bearer bootstrap-token"}, json={"user_id": "bootstrap-admin"})
    token = resp.json()["token"]

    # Verify works
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200

    # Revoke
    revoke_resp = client.request(
        "DELETE",
        "/api/auth/token",
        content=json.dumps({"token": token}),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    assert revoke_resp.status_code == 200

    # Verify fails after revoke
    me_resp2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp2.status_code == 401


def test_auth_revoke_nonexistent_token_returns_404() -> None:
    """Verify that revoking a nonexistent token returns 404."""
    auth = TokenAuth()
    auth.register_token("bootstrap-token", "bootstrap-admin")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    resp = client.request(
        "DELETE",
        "/api/auth/token",
        content=json.dumps({"token": "sf_nonexistent"}),
        headers={"Content-Type": "application/json", "Authorization": "Bearer bootstrap-token"},
    )
    assert resp.status_code == 404


def test_auth_revoke_requires_authenticated_self_revocation() -> None:
    auth = TokenAuth()
    auth.register_token("owner-token", "owner")
    auth.register_token("other-token", "other")
    client = TestClient(create_app(store=InMemoryStoryForgeStore(), auth=auth))

    missing = client.request("DELETE", "/api/auth/token", content=json.dumps({"token": "owner-token"}), headers={"Content-Type": "application/json"})
    other_user = client.request(
        "DELETE",
        "/api/auth/token",
        content=json.dumps({"token": "owner-token"}),
        headers={"Content-Type": "application/json", "Authorization": "Bearer other-token"},
    )

    assert missing.status_code == 401
    assert other_user.status_code == 403
    assert auth.verify("owner-token") == "owner"


def test_asset_comments_add_and_list() -> None:
    """Verify that comments can be added to assets and listed."""
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    project_response = client.post("/api/projects", json={"idea": "Test project for comments"})
    assert project_response.status_code == 200
    project_id = project_response.json()["project_id"]

    # Create an asset
    asset_resp = client.post(f"/api/projects/{project_id}/assets", json={"asset_type": "brief", "content": "Brief content"})
    assert asset_resp.status_code == 200
    asset_id = asset_resp.json()["asset_id"]

    # Initially no comments
    list_resp = client.get(f"/api/projects/{project_id}/assets/{asset_id}/comments")
    assert list_resp.status_code == 200
    assert list_resp.json() == []

    # Add a comment
    add_resp = client.post(f"/api/projects/{project_id}/assets/{asset_id}/comments", json={"author": "Alice", "content": "Nice brief!"})
    assert add_resp.status_code == 200
    comment = add_resp.json()
    assert comment["author"] == "Alice"
    assert comment["content"] == "Nice brief!"
    assert "id" in comment
    assert "created_at" in comment

    # List shows the comment
    list_resp2 = client.get(f"/api/projects/{project_id}/assets/{asset_id}/comments")
    assert len(list_resp2.json()) == 1
    assert list_resp2.json()[0]["author"] == "Alice"

    # Add another comment
    client.post(f"/api/projects/{project_id}/assets/{asset_id}/comments", json={"author": "Bob", "content": "Looks good"})
    list_resp3 = client.get(f"/api/projects/{project_id}/assets/{asset_id}/comments")
    assert len(list_resp3.json()) == 2


def test_asset_comments_anchor_status_resolve_and_branch_scope() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="anchored comments", branches=["main", "alt"]))
    alt_asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="First paragraph.\n\nSecond paragraph.",
        )
    )

    invalid_anchor = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments",
        json={"branch": "alt", "author": "Eve", "content": "bad", "anchor_type": "paragraph", "paragraph_index": -1},
    )
    invalid_paragraph_anchor = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments",
        json={"branch": "alt", "author": "Eve", "content": "bad", "anchor_type": "paragraph", "paragraph_index": 99},
    )
    invalid_text_anchor = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments",
        json={"branch": "alt", "author": "Eve", "content": "bad", "anchor_type": "text", "start_offset": 0, "end_offset": 999},
    )
    invalid_status = client.put(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}",
        json={"branch": "alt", "content": "bad status", "approval_status": "done"},
    )
    wrong_branch_add = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments",
        json={"branch": "main", "author": "Eve", "content": "wrong branch"},
    )
    add_response = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments",
        json={
            "branch": "alt",
            "author": "Lin",
            "content": "Strengthen the transition.",
            "anchor_type": "paragraph",
            "paragraph_index": 1,
            "start_offset": 0,
            "end_offset": 8,
            "actor_id": "editor-lin",
            "actor_name": "Lin",
            "role": "editor",
        },
    )

    assert invalid_anchor.status_code == 422
    assert invalid_paragraph_anchor.status_code == 400
    assert invalid_text_anchor.status_code == 400
    assert invalid_status.status_code == 422
    assert wrong_branch_add.status_code == 404
    assert add_response.status_code == 200
    comment = add_response.json()
    assert comment["status"] == "open"
    assert comment["anchor_type"] == "paragraph"
    assert comment["paragraph_index"] == 1
    assert comment["start_offset"] == 0
    assert comment["end_offset"] == 8
    assert comment["actor_id"] == "editor-lin"
    assert comment["actor_name"] == "Lin"
    assert comment["role"] == "editor"

    wrong_branch_list = client.get(f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments?branch=main")
    open_comments = client.get(f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments?branch=alt&status=open")
    assert wrong_branch_list.status_code == 404
    assert open_comments.status_code == 200
    assert [item["id"] for item in open_comments.json()] == [comment["id"]]

    wrong_branch_resolve = client.patch(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments/{comment['id']}",
        json={"branch": "main", "status": "resolved"},
    )
    resolve_response = client.patch(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments/{comment['id']}",
        json={"branch": "alt", "status": "resolved", "role": "reviewer"},
    )

    assert wrong_branch_resolve.status_code == 404
    assert resolve_response.status_code == 200
    resolved_comment = resolve_response.json()
    assert resolved_comment["status"] == "resolved"
    assert resolved_comment["role"] == "reviewer"
    assert "resolved_at" in resolved_comment

    open_after_resolve = client.get(f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments?branch=alt&status=open")
    resolved_comments = client.get(f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/comments?branch=alt&status=resolved")
    assert open_after_resolve.status_code == 200
    assert open_after_resolve.json() == []
    assert resolved_comments.status_code == 200
    assert [item["id"] for item in resolved_comments.json()] == [comment["id"]]


def test_spot_fix_endpoint_creates_task() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))

    project = store.create_project(Project(idea="test"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
            structured_data={"chapter_number": 1, "title": "Ch1"},
        )
    )

    resp = client.post(
        f"/api/projects/{project.project_id}/spot-fix",
        json={
            "chapter_asset_id": chapter.asset_id,
            "paragraph_indices": [0, 2],
            "fix_instruction": "Make it more dramatic",
        },
    )
    assert resp.status_code == 200
    task = resp.json()
    assert task["task_type"] == "spot_fix"
    assert task["status"] in ("accepted", "queued")
    assert task["payload"]["fix_instruction"] == "Make it more dramatic"
    assert chapter.asset_id in task["input_asset_refs"]


@pytest.mark.parametrize(
    ("payload_override", "expected_status"),
    [
        ({"fix_instruction": "   "}, 400),
        ({"paragraph_indices": []}, 400),
        ({"paragraph_indices": [-1]}, 400),
        ({"paragraph_indices": [3]}, 400),
        ({"paragraph_indices": ["0"]}, 400),
        ({"paragraph_indices": [1.2]}, 400),
    ],
)
def test_generic_spot_fix_task_validation_matches_dedicated_endpoint(payload_override: dict, expected_status: int) -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))

    project = store.create_project(Project(idea="test"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
            structured_data={"chapter_number": 1, "title": "Ch1"},
        )
    )
    payload = {"chapter_asset_id": chapter.asset_id, "paragraph_indices": [0], "fix_instruction": "tighten"} | payload_override

    resp = client.post(
        f"/api/projects/{project.project_id}/tasks",
        json={"task_type": "spot_fix", "payload": payload},
    )

    assert resp.status_code == expected_status


def test_generic_spot_fix_task_rejects_spot_fix_candidate_input() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))

    project = store.create_project(Project(idea="test"))
    original = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="Paragraph one."))
    candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            content="Fixed paragraph.",
            structured_data={"is_spot_fix": True, "original_asset_id": original.asset_id, "spot_fix_paragraphs": [0]},
        )
    )

    resp = client.post(
        f"/api/projects/{project.project_id}/tasks",
        json={
            "task_type": "spot_fix",
            "payload": {"chapter_asset_id": candidate.asset_id, "paragraph_indices": [0], "fix_instruction": "tighten"},
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot spot-fix a spot-fix candidate"


def test_generic_spot_fix_task_accepts_valid_payload() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))

    project = store.create_project(Project(idea="test"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Paragraph one.\n\nParagraph two.",
            structured_data={"chapter_number": 1, "title": "Ch1"},
        )
    )

    resp = client.post(
        f"/api/projects/{project.project_id}/tasks",
        json={
            "task_type": "spot_fix",
            "payload": {"chapter_asset_id": chapter.asset_id, "paragraph_indices": [1], "fix_instruction": "tighten"},
        },
    )

    assert resp.status_code == 200
    task = resp.json()
    assert task["task_type"] == "spot_fix"
    assert task["payload"]["branch"] == "main"


def test_spot_fix_generator_replaces_paragraphs() -> None:
    from storyforge.config import LlmConfig
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.generators import StoryForgeGenerators
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="test"))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            source="system",
            content="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
            structured_data={"chapter_number": 1, "title": "Ch1"},
        )
    )
    generators = StoryForgeGenerators(store, LlmConfig(skip_llm=True))

    fixed = generators.generate_spot_fix(project, chapter, [0, 2], "Replace first and third paragraphs")

    assert fixed.structured_data["is_spot_fix"] is True
    assert fixed.structured_data["spot_fix_paragraphs"] == [0, 2]
    assert "Paragraph two." in fixed.content
    assert "定点修复" in fixed.content


def test_auto_mode_toggle() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))

    project = store.create_project(Project(idea="test"))
    assert project.auto_mode is True

    resp = client.patch(f"/api/projects/{project.project_id}/auto-mode", json={"auto_mode": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_mode"] is False

    # Verify persistence
    resp2 = client.get(f"/api/projects/{project.project_id}")
    assert resp2.json()["auto_mode"] is False


def test_assets_are_isolated_by_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="branch isolation", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main chapter"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt chapter"))

    main_assets = store.list_assets(project.project_id, AssetType.chapter, branch="main")
    alt_assets = store.list_assets(project.project_id, AssetType.chapter, branch="alt")

    assert [asset.content for asset in main_assets] == ["main chapter"]
    assert [asset.content for asset in alt_assets] == ["alt chapter"]
    assert store.get_latest_asset(project.project_id, AssetType.chapter, branch="main").content == "main chapter"
    assert store.get_latest_asset(project.project_id, AssetType.chapter, branch="alt").content == "alt chapter"


def test_assets_api_filters_by_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch api", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main chapter"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt chapter"))

    main_resp = client.get(f"/api/projects/{project.project_id}/assets?branch=main")
    alt_resp = client.get(f"/api/projects/{project.project_id}/assets?branch=alt")

    assert main_resp.status_code == 200
    assert alt_resp.status_code == 200
    assert [asset["content"] for asset in main_resp.json()] == ["main chapter"]
    assert [asset["content"] for asset in alt_resp.json()] == ["alt chapter"]


def test_tasks_are_isolated_by_branch() -> None:
    from storyforge.domain.models import Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="task branch isolation", branches=["main", "alt"]))
    main_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main"))
    alt_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="alt"))

    assert [task.task_id for task in store.list_tasks(project.project_id, branch="main")] == [main_task.task_id]
    assert [task.task_id for task in store.list_tasks(project.project_id, branch="alt")] == [alt_task.task_id]


def test_first_loop_tasks_api_filters_by_branch() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch task api", branches=["main", "alt"]))

    main_resp = client.post(f"/api/projects/{project.project_id}/runs/first-loop", json={"chapter_number": 1, "branch": "main"})
    alt_resp = client.post(f"/api/projects/{project.project_id}/runs/first-loop", json={"chapter_number": 1, "branch": "alt"})

    assert main_resp.status_code == 200
    assert alt_resp.status_code == 200
    assert {task["branch"] for task in main_resp.json()} == {"main"}
    assert {task["branch"] for task in alt_resp.json()} == {"alt"}

    main_tasks = client.get(f"/api/projects/{project.project_id}/tasks?branch=main")
    alt_tasks = client.get(f"/api/projects/{project.project_id}/tasks?branch=alt")

    assert main_tasks.status_code == 200
    assert alt_tasks.status_code == 200
    assert [task["task_id"] for task in main_tasks.json()] == [task["task_id"] for task in main_resp.json()]
    assert [task["task_id"] for task in alt_tasks.json()] == [task["task_id"] for task in alt_resp.json()]


def test_first_loop_outputs_are_isolated_by_branch() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch pipeline", branches=["main", "alt"]))

    response = client.post(
        f"/api/projects/{project.project_id}/runs/first-loop",
        json={"chapter_number": 1, "branch": "alt", "auto_run": True},
    )

    assert response.status_code == 200
    assert {task["branch"] for task in response.json()} == {"alt"}
    assert {asset.asset_type.value for asset in store.list_assets(project.project_id, branch="main")} == set()
    assert [asset.asset_type.value for asset in store.list_assets(project.project_id, branch="alt")] == [
        "brief",
        "outline",
        "chapter",
        "review_note",
    ]

    alt_tasks = store.list_tasks(project.project_id, branch="alt")
    alt_asset_ids = {asset.asset_id for asset in store.list_assets(project.project_id, branch="alt")}
    for task in alt_tasks:
        assert set(task.input_asset_refs).issubset(alt_asset_ids)
        assert set(task.output_refs).issubset(alt_asset_ids)


def test_production_console_reports_mode_queue_and_actions() -> None:
    from storyforge.domain.models import Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="production console", branches=["main", "alt"], auto_mode=False))
    store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main"))
    store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="alt"))

    response = client.get(f"/api/projects/{project.project_id}/production-console?branch=alt")

    assert response.status_code == 200
    console = response.json()
    assert console["project_id"] == project.project_id
    assert console["branch"] == "alt"
    assert console["auto_mode"] is False
    assert console["task_summary"] == {
        "total": 1,
        "accepted": 1,
        "queued": 0,
        "running": 0,
        "waiting_retry": 0,
        "failed": 0,
        "cancelled": 0,
        "completed": 0,
    }
    assert console["queue"][0]["branch"] == "alt"
    assert console["available_actions"] == ["queue_first_loop", "process_next", "drain"]


def test_rejected_review_rewrite_preserves_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, EventType, Project, TaskRecord, TaskStatus, TaskType
    from storyforge.execution.state_machine import TaskStateMachine
    from storyforge.execution.store import InMemoryStoryForgeStore
    from storyforge.execution.workflow import ClosedLoopService

    store = InMemoryStoryForgeStore()
    service = ClosedLoopService(store, TaskStateMachine())
    project = store.create_project(Project(idea="rewrite branch", branches=["main", "alt"]))
    brief = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, branch="alt", content="brief"))
    outline = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.outline, branch="alt", content="outline"))
    chapter = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="draft"))
    review_task = store.create_task(
        TaskRecord(
            project_id=project.project_id,
            task_type=TaskType.chapter_review,
            branch="alt",
            status=TaskStatus.completed,
            input_asset_refs=[chapter.asset_id],
            payload={"chapter_number": 1, "branch": "alt"},
        )
    )
    review = Asset(
        project_id=project.project_id,
        asset_type=AssetType.review_note,
        branch="alt",
        structured_data={"approved": False, "issues": ["fix pacing"]},
    )

    service._handle_review_continuation(review_task, review)

    rewrite_tasks = [task for task in store.list_tasks(project.project_id, branch="alt") if task.task_type == TaskType.chapter_generation]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].branch == "alt"
    assert rewrite_tasks[0].payload["branch"] == "alt"
    assert chapter.asset_id in rewrite_tasks[0].input_asset_refs
    assert outline.asset_id in rewrite_tasks[0].input_asset_refs
    assert brief.asset_id in rewrite_tasks[0].input_asset_refs
    assert store.list_events(rewrite_tasks[0].task_id)[0].event_type == EventType.accepted


def test_branch_lineage_expected_version_isolated_from_forked_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch lineage lock", branches=["main", "alt"]))
    main_asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main v1"))
    forked_asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="alt v1",
            structured_data={"origin_asset_id": main_asset.asset_id},
        ),
        lineage_origin_id=main_asset.asset_id,
    )
    alt_update = client.put(
        f"/api/projects/{project.project_id}/assets/{forked_asset.asset_id}",
        json={"branch": "alt", "content": "alt v2", "base_version": forked_asset.version},
    )
    main_update = client.put(
        f"/api/projects/{project.project_id}/assets/{main_asset.asset_id}",
        json={"branch": "main", "content": "main v2", "base_version": main_asset.version},
    )

    assert alt_update.status_code == 200
    assert main_update.status_code == 200
    assert main_update.json()["branch"] == "main"
    assert main_update.json()["content"] == "main v2"


def test_asset_update_records_role_and_approval_status() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="single user role flow"))
    asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            content="draft",
            comments=[{"id": "cmt_existing", "author": "Lin", "content": "keep this", "status": "open"}],
        )
    )

    reviewed = client.put(
        f"/api/projects/{project.project_id}/assets/{asset.asset_id}",
        json={
            "branch": "main",
            "content": "reviewed draft",
            "actor_id": "solo-user",
            "actor_name": "Solo User",
            "role": "editor",
            "approval_status": "reviewed",
        },
    )
    approved = client.put(
        f"/api/projects/{project.project_id}/assets/{reviewed.json()['asset_id']}",
        json={
            "branch": "main",
            "content": "approved draft",
            "actor_id": "solo-user",
            "actor_name": "Solo User",
            "role": "approver",
            "approval_status": "approved",
        },
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["structured_data"]["actor_id"] == "solo-user"
    assert reviewed.json()["structured_data"]["actor_name"] == "Solo User"
    assert reviewed.json()["structured_data"]["role"] == "editor"
    assert reviewed.json()["structured_data"]["approval_status"] == "reviewed"
    assert reviewed.json()["comments"] == asset.comments
    assert approved.status_code == 200
    assert approved.json()["structured_data"]["actor_id"] == "solo-user"
    assert approved.json()["structured_data"]["role"] == "approver"
    assert approved.json()["structured_data"]["approval_status"] == "approved"


def test_asset_update_rollback_and_comments_preserve_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch asset derivatives", branches=["main", "alt"]))
    asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="v1"))

    update = client.put(
        f"/api/projects/{project.project_id}/assets/{asset.asset_id}",
        json={"branch": "alt", "content": "v2", "base_version": asset.version},
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["branch"] == "alt"

    comment = client.post(
        f"/api/projects/{project.project_id}/assets/{updated['asset_id']}/comments",
        json={"branch": "alt", "author": "editor", "content": "note"},
    )
    assert comment.status_code == 200
    commented = store.get_asset_by_id(updated["asset_id"])
    assert commented is not None
    assert commented.branch == "alt"

    rollback = client.post(
        f"/api/projects/{project.project_id}/assets/{updated['asset_id']}/rollback",
        json={"branch": "alt", "target_version": asset.version},
    )
    assert rollback.status_code == 200
    assert rollback.json()["branch"] == "alt"
    assert not store.list_assets(project.project_id, branch="main")


def test_latest_asset_api_filters_by_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="latest branch", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main latest"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt latest"))

    main_resp = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=main")
    alt_resp = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt")

    assert main_resp.status_code == 200
    assert alt_resp.status_code == 200
    assert main_resp.json()["content"] == "main latest"
    assert alt_resp.json()["content"] == "alt latest"


def test_asset_versions_diff_and_rollback_are_branch_scoped() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="version branch", branches=["main", "alt"]))
    main_v1 = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Main old\n"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Main new\n"))
    alt_v1 = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="Alt old\n"))
    alt_v2 = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="Alt new\n"))

    versions = client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=alt")
    assert versions.status_code == 200
    assert [asset["content"] for asset in versions.json()] == ["Alt old\n", "Alt new\n"]

    diff = client.post(f"/api/projects/{project.project_id}/assets/chapter/diff?branch=main")
    assert diff.status_code == 200
    assert "-Main old" in diff.text
    assert "+Main new" in diff.text
    assert "Alt" not in diff.text

    cross_branch_rollback = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_v2.asset_id}/rollback",
        json={"target_version": main_v1.version},
    )
    assert cross_branch_rollback.status_code == 404

    rollback = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_v2.asset_id}/rollback",
        json={"branch": "alt", "target_version": alt_v1.version},
    )
    assert rollback.status_code == 200
    assert rollback.json()["branch"] == "alt"
    assert rollback.json()["content"] == "Alt old\n"


def test_export_and_chapters_are_branch_scoped() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch export", title="Branch Book", branches=["main", "alt"]))
    main = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Main body",
            structured_data={"chapter_number": 1, "title": "Main"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Alt body",
            structured_data={"chapter_number": 1, "title": "Alt"},
        )
    )

    export_response = client.post(f"/api/projects/{project.project_id}/export", json={"branch": "main"})
    chapters_response = client.get(f"/api/projects/{project.project_id}/chapters?branch=main")

    assert export_response.status_code == 200
    assert "Main body" in export_response.text
    assert "Alt body" not in export_response.text
    assert chapters_response.status_code == 200
    assert chapters_response.json() == [
        {"chapter_number": 1, "title": "Main", "status": "pending", "asset_id": main.asset_id, "review_approved": None}
    ]


def test_export_and_chapters_ignore_deleted_assets() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="deleted export", title="Visible Book", branches=["main", "alt"]))
    visible = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Visible body",
            structured_data={"chapter_number": 1, "title": "Visible"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Deleted body",
            structured_data={"chapter_number": 2, "title": "Deleted"},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            structured_data={"chapter_number": 1, "approved": False},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )

    export_response = client.post(f"/api/projects/{project.project_id}/export", json={"branch": "main"})
    chapters_response = client.get(f"/api/projects/{project.project_id}/chapters?branch=main")

    assert export_response.status_code == 200
    assert "Visible body" in export_response.text
    assert "Deleted body" not in export_response.text
    assert chapters_response.status_code == 200
    assert chapters_response.json() == [
        {"chapter_number": 1, "title": "Visible", "status": "pending", "asset_id": visible.asset_id, "review_approved": None}
    ]


def test_deleted_versions_diff_and_comments_are_hidden() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="deleted versions", branches=["main", "alt"]))
    active_old = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Active old\n", structured_data={"chapter_number": 1}))
    deleted = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Deleted secret\n",
            structured_data={"chapter_number": 2},
            comments=[{"author": "editor", "content": "private note"}],
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )
    active_new = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Active new\n", structured_data={"chapter_number": 3}))

    versions = client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=main")
    diff = client.post(f"/api/projects/{project.project_id}/assets/chapter/diff?branch=main")
    comments = client.get(f"/api/projects/{project.project_id}/assets/{deleted.asset_id}/comments")

    assert versions.status_code == 200
    assert [asset["asset_id"] for asset in versions.json()] == [active_old.asset_id, active_new.asset_id]
    assert diff.status_code == 200
    assert "Deleted secret" not in diff.text
    assert "-Active old" in diff.text
    assert "+Active new" in diff.text
    assert comments.status_code == 404


def test_trash_is_branch_scoped() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="trash branch", branches=["main", "alt"]))
    main_deleted = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main deleted", is_deleted=True, deleted_at=utc_now()))
    alt_deleted = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt deleted", is_deleted=True, deleted_at=utc_now()))

    main_trash = client.get(f"/api/projects/{project.project_id}/assets/trash?branch=main")
    alt_trash = client.get(f"/api/projects/{project.project_id}/assets/trash?branch=alt")

    assert main_trash.status_code == 200
    assert [asset["asset_id"] for asset in main_trash.json()] == [main_deleted.asset_id]
    assert alt_trash.status_code == 200
    assert [asset["asset_id"] for asset in alt_trash.json()] == [alt_deleted.asset_id]


def test_deleted_assets_cannot_be_updated_or_rolled_back() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="deleted mutation", branches=["main", "alt"]))
    active_old = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Active old\n", structured_data={"chapter_number": 1}))
    deleted = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Deleted secret\n", structured_data={"chapter_number": 2}, is_deleted=True, deleted_at=utc_now()))
    active_new = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="Active new\n", structured_data={"chapter_number": 3}))

    update_deleted = client.put(f"/api/projects/{project.project_id}/assets/{deleted.asset_id}", json={"content": "Resurrected"})
    rollback_deleted_asset = client.post(f"/api/projects/{project.project_id}/assets/{deleted.asset_id}/rollback", json={"target_version": active_old.version})
    rollback_to_deleted_version = client.post(f"/api/projects/{project.project_id}/assets/{active_new.asset_id}/rollback", json={"target_version": deleted.version})

    assert update_deleted.status_code == 404
    assert rollback_deleted_asset.status_code == 404
    assert rollback_to_deleted_version.status_code == 404


def test_spot_fix_rejects_deleted_chapter() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="deleted spot fix", branches=["main", "alt"]))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Paragraph one.\n\nParagraph two.",
            structured_data={"chapter_number": 1},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )

    response = client.post(
        f"/api/projects/{project.project_id}/spot-fix",
        json={"chapter_asset_id": chapter.asset_id, "paragraph_indices": [0], "fix_instruction": "tighten", "branch": "main"},
    )

    assert response.status_code == 404


def test_asset_mutations_validate_explicit_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch guarded mutations", branches=["main", "alt"]))
    alt_asset = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="Alt v1"))
    deleted_alt_asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Deleted alt",
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )

    update_wrong_branch = client.put(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}",
        json={"branch": "main", "content": "Cross-branch overwrite"},
    )
    rollback_wrong_branch = client.post(
        f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}/rollback",
        json={"branch": "main", "target_version": alt_asset.version},
    )
    delete_wrong_branch = client.delete(f"/api/projects/{project.project_id}/assets/{alt_asset.asset_id}?branch=main")
    restore_wrong_branch = client.post(f"/api/projects/{project.project_id}/assets/{deleted_alt_asset.asset_id}/restore?branch=main")

    assert update_wrong_branch.status_code == 404
    assert rollback_wrong_branch.status_code == 404
    assert delete_wrong_branch.status_code == 404
    assert restore_wrong_branch.status_code == 404
    assert store.get_asset_by_id(alt_asset.asset_id).is_deleted is False
    assert store.get_asset_by_id(deleted_alt_asset.asset_id).is_deleted is True


def test_deleting_latest_asset_hides_entire_lineage_from_active_surfaces() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="lineage delete", title="Lineage Book", branches=["main", "alt"]))
    original = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Old body",
            structured_data={"chapter_number": 1, "title": "Old"},
        )
    )
    updated = client.put(
        f"/api/projects/{project.project_id}/assets/{original.asset_id}",
        json={"branch": "main", "content": "Latest body", "structured_data": {"title": "Latest"}},
    ).json()

    delete_response = client.delete(f"/api/projects/{project.project_id}/assets/{updated['asset_id']}?branch=main")
    latest_response = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=main")
    chapters_response = client.get(f"/api/projects/{project.project_id}/chapters?branch=main")
    export_response = client.post(f"/api/projects/{project.project_id}/export", json={"branch": "main"})
    versions_response = client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=main")
    trash_response = client.get(f"/api/projects/{project.project_id}/assets/trash?branch=main")

    assert delete_response.status_code == 200
    assert latest_response.status_code == 404
    assert chapters_response.status_code == 200
    assert chapters_response.json() == []
    assert export_response.status_code == 200
    assert "Old body" not in export_response.text
    assert "Latest body" not in export_response.text
    assert versions_response.json() == []
    assert {asset["asset_id"] for asset in trash_response.json()} == {original.asset_id, updated["asset_id"]}


def test_deleting_accepted_spot_fix_hides_original_lineage() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="spot fix lineage delete", title="Spot Book", branches=["main", "alt"]))
    original = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Original paragraph.",
            structured_data={"chapter_number": 1, "title": "Original"},
        )
    )
    candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Fixed paragraph.",
            structured_data={"is_spot_fix": True, "original_asset_id": original.asset_id, "spot_fix_paragraphs": [0], "chapter_number": 1, "title": "Fixed"},
        )
    )
    accepted = client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/accept?branch=alt")

    delete_response = client.delete(f"/api/projects/{project.project_id}/assets/{accepted.json()['asset_id']}?branch=alt")
    latest_response = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt")
    chapters_response = client.get(f"/api/projects/{project.project_id}/chapters?branch=alt")
    export_response = client.post(f"/api/projects/{project.project_id}/export", json={"branch": "alt"})
    assets_response = client.get(f"/api/projects/{project.project_id}/assets?branch=alt")
    versions_response = client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=alt")
    trash_response = client.get(f"/api/projects/{project.project_id}/assets/trash?branch=alt")

    assert accepted.status_code == 200
    assert accepted.json()["structured_data"]["origin_asset_id"] == original.asset_id
    assert delete_response.status_code == 200
    assert latest_response.status_code == 404
    assert chapters_response.json() == []
    assert "Original paragraph" not in export_response.text
    assert "Fixed paragraph" not in export_response.text
    assert assets_response.json() == []
    assert versions_response.json() == []
    assert {asset["asset_id"] for asset in trash_response.json()} == {original.asset_id, candidate.asset_id, accepted.json()["asset_id"]}


def test_spot_fix_candidate_actions_reject_deleted_candidate_or_original() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="deleted spot candidates", branches=["main", "alt"]))
    active_original = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Original paragraph.",
            structured_data={"chapter_number": 1},
        )
    )
    deleted_candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Candidate paragraph.",
            structured_data={"is_spot_fix": True, "original_asset_id": active_original.asset_id, "spot_fix_paragraphs": [0]},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )
    deleted_original = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Deleted original.",
            structured_data={"chapter_number": 2},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )
    candidate_for_deleted_original = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="Candidate for deleted original.",
            structured_data={"is_spot_fix": True, "original_asset_id": deleted_original.asset_id, "spot_fix_paragraphs": [0]},
        )
    )

    deleted_candidate_responses = [
        client.get(f"/api/projects/{project.project_id}/spot-fix/{deleted_candidate.asset_id}/diff?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{deleted_candidate.asset_id}/accept?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{deleted_candidate.asset_id}/reject?branch=main"),
    ]
    deleted_original_responses = [
        client.get(f"/api/projects/{project.project_id}/spot-fix/{candidate_for_deleted_original.asset_id}/diff?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate_for_deleted_original.asset_id}/accept?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate_for_deleted_original.asset_id}/reject?branch=main"),
    ]

    assert [response.status_code for response in deleted_candidate_responses] == [404, 404, 404]
    assert [response.status_code for response in deleted_original_responses] == [404, 404, 404]


def test_spot_fix_task_preserves_chapter_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="spot fix branch", branches=["main", "alt"]))
    chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Paragraph one.\n\nParagraph two.",
        )
    )

    response = client.post(
        f"/api/projects/{project.project_id}/spot-fix",
        json={"chapter_asset_id": chapter.asset_id, "paragraph_indices": [1], "fix_instruction": "tighten"},
    )

    assert response.status_code == 200
    task = response.json()
    assert task["branch"] == "alt"
    assert task["payload"]["branch"] == "alt"


def test_pacing_curve_aggregates_current_branch_reviews() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="pacing curve", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main", structured_data={"chapter_number": 1, "title": "Main"}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="main", content="main review", structured_data={"chapter_number": 1, "quality_details": {"pacing": {"passed": False, "details": {"score": 0.1, "conflict_intensity": 0.2, "emotional_intensity": 0.3}}}}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt one", structured_data={"chapter_number": 1, "title": "Alt One"}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt two", structured_data={"chapter_number": 2, "title": "Alt Two"}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="alt",
            content="alt review",
            structured_data={
                "chapter_number": 1,
                "quality_details": {"pacing": {"passed": True, "details": {"score": 0.8, "conflict_intensity": 0.7, "emotional_intensity": 0.6}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve?branch=alt")

    assert response.status_code == 200
    body = response.json()
    assert body["branch"] == "alt"
    assert [point["chapter_number"] for point in body["points"]] == [1, 2]
    assert body["points"][0]["title"] == "Alt One"
    assert body["points"][0]["pacing_score"] == 0.8
    assert body["points"][0]["conflict_intensity"] == 0.7
    assert body["points"][0]["emotional_intensity"] == 0.6
    assert body["points"][0]["status"] == "known"
    assert body["points"][1]["title"] == "Alt Two"
    assert body["points"][1]["status"] == "unknown"
    assert body["points"][1]["pacing_score"] is None


def test_pacing_curve_derives_metrics_from_generated_review_details() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="generated pacing"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="chapter", structured_data={"chapter_number": 1, "title": "Generated"}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="generated review",
            structured_data={
                "chapter_number": 1,
                "quality_details": {"pacing": {"passed": True, "details": {"dialogue_ratio": 0.25, "sentence_count": 12, "scene_transitions": 2}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["status"] == "known"
    assert point["pacing_score"] == 0.76
    assert point["conflict_intensity"] == 0.667
    assert point["emotional_intensity"] == 0.5


def test_pacing_curve_does_not_reuse_stale_review_for_newer_chapter_revision() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="stale pacing"))
    original = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="old", structured_data={"chapter_number": 1, "title": "Old"})
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="review old",
            structured_data={
                "chapter_number": 1,
                "chapter_ref": original.asset_id,
                "quality_details": {"pacing": {"details": {"score": 0.9, "conflict_intensity": 0.8, "emotional_intensity": 0.7}}},
            },
        )
    )
    store.save_asset(
        Asset(asset_id=original.asset_id, project_id=project.project_id, asset_type=AssetType.chapter, content="new", structured_data={"chapter_number": 1, "title": "New"}),
        lineage_origin_id=original.asset_id,
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["title"] == "New"
    assert point["status"] == "unknown"
    assert point["review_asset_id"] is None
    assert point["pacing_score"] is None


def test_pacing_curve_rejects_late_stale_review_for_same_asset_id_revision() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="late stale pacing"))
    original = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="old", structured_data={"chapter_number": 1, "title": "Old"})
    )
    store.save_asset(
        Asset(asset_id=original.asset_id, project_id=project.project_id, asset_type=AssetType.chapter, content="new", structured_data={"chapter_number": 1, "title": "New"}),
        lineage_origin_id=original.asset_id,
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="late stale review",
            structured_data={
                "chapter_number": 1,
                "chapter_ref": original.asset_id,
                "chapter_version": original.version,
                "quality_details": {"pacing": {"details": {"score": 0.9, "conflict_intensity": 0.8, "emotional_intensity": 0.7}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["title"] == "New"
    assert point["status"] == "unknown"
    assert point["review_asset_id"] is None


def test_pacing_curve_uses_newest_matching_review_when_newer_review_is_stale() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="matching pacing"))
    original = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="old", structured_data={"chapter_number": 1, "title": "Old"})
    )
    latest = store.save_asset(
        Asset(asset_id=original.asset_id, project_id=project.project_id, asset_type=AssetType.chapter, content="new", structured_data={"chapter_number": 1, "title": "New"}),
        lineage_origin_id=original.asset_id,
    )
    current_review = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="current review",
            structured_data={
                "chapter_number": 1,
                "chapter_ref": latest.asset_id,
                "chapter_version": latest.version,
                "quality_details": {"pacing": {"details": {"score": 0.6, "conflict_intensity": 0.5, "emotional_intensity": 0.4}}},
            },
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="newer stale review",
            structured_data={
                "chapter_number": 1,
                "chapter_ref": original.asset_id,
                "chapter_version": original.version,
                "quality_details": {"pacing": {"details": {"score": 0.9, "conflict_intensity": 0.8, "emotional_intensity": 0.7}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["title"] == "New"
    assert point["status"] == "known"
    assert point["review_asset_id"] == current_review.asset_id
    assert point["pacing_score"] == 0.6


def test_pacing_curve_rejects_legacy_review_without_revision_for_revised_chapter() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="legacy stale pacing"))
    original = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="old", structured_data={"chapter_number": 1, "title": "Old"})
    )
    store.save_asset(
        Asset(asset_id=original.asset_id, project_id=project.project_id, asset_type=AssetType.chapter, content="new", structured_data={"chapter_number": 1, "title": "New"}),
        lineage_origin_id=original.asset_id,
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="legacy review",
            structured_data={
                "chapter_number": 1,
                "quality_details": {"pacing": {"details": {"score": 0.7, "conflict_intensity": 0.6, "emotional_intensity": 0.5}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["title"] == "New"
    assert point["status"] == "unknown"
    assert point["review_asset_id"] is None


def test_pacing_curve_matches_review_by_chapter_version_without_chapter_ref() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="version only pacing"))
    chapter = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="chapter", structured_data={"chapter_number": 1, "title": "Version Only"})
    )
    review = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="version-only review",
            structured_data={
                "chapter_number": 1,
                "chapter_version": chapter.version,
                "quality_details": {"pacing": {"details": {"score": 0.7, "conflict_intensity": 0.6, "emotional_intensity": 0.5}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["status"] == "known"
    assert point["review_asset_id"] == review.asset_id
    assert point["pacing_score"] == 0.7


def test_pacing_curve_uses_latest_version_per_chapter_number() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="versioned pacing"))
    original = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="old", structured_data={"chapter_number": 1, "title": "Old"})
    )
    store.save_asset(
        Asset(asset_id=original.asset_id, project_id=project.project_id, asset_type=AssetType.chapter, content="new", structured_data={"chapter_number": 1, "title": "New"}),
        lineage_origin_id=original.asset_id,
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == 1
    assert points[0]["chapter_number"] == 1
    assert points[0]["title"] == "New"


def test_pacing_curve_handles_malformed_review_quality_details() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store), raise_server_exceptions=False)
    project = store.create_project(Project(idea="malformed pacing"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="chapter", structured_data={"chapter_number": 1, "title": "Malformed"}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="bad review",
            structured_data={"chapter_number": 1, "quality_details": {"pacing": {"details": "not a dict"}}},
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["status"] == "unknown"
    assert point["pacing_score"] is None
    assert point["conflict_intensity"] is None
    assert point["emotional_intensity"] is None


def test_pacing_curve_sanitizes_bad_numeric_review_metrics() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store), raise_server_exceptions=False)
    project = store.create_project(Project(idea="bad numbers"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, content="chapter", structured_data={"chapter_number": 1, "title": "Bad Numbers"}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            content="bad numbers",
            structured_data={
                "chapter_number": 1,
                "quality_details": {"pacing": {"details": {"score": float("nan"), "conflict_intensity": 4, "emotional_intensity": -2}}},
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/pacing-curve")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["status"] == "unknown"
    assert point["pacing_score"] is None
    assert point["conflict_intensity"] == 1.0
    assert point["emotional_intensity"] == 0.0


def test_latest_review_api_filters_by_branch_and_chapter() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="review branch", branches=["main", "alt"]))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="main review",
            structured_data={"chapter_number": 1, "approved": False, "issues": ["main issue"]},
        )
    )
    alt_old = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="alt",
            content="alt old review",
            structured_data={"chapter_number": 1, "approved": False, "issues": ["old issue"]},
        )
    )
    alt_latest = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="alt",
            content="alt latest review",
            structured_data={
                "chapter_number": 1,
                "approved": False,
                "failed_checks": ["quality:pacing"],
                "issues": ["alt issue"],
                "quality_details": {"pacing": {"passed": False, "details": {"dialogue_ratio": 0}}},
                "rule_audits": [{"rule_id": "rule_x", "name": "No X", "layer": "custom", "passed": False, "issues": ["x"]}],
            },
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="alt",
            content="alt chapter two review",
            structured_data={"chapter_number": 2, "approved": True, "issues": []},
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/reviews/latest?branch=alt&chapter_number=1")

    assert response.status_code == 200
    assert response.json()["asset_id"] == alt_latest.asset_id
    assert response.json()["asset_id"] != alt_old.asset_id
    assert response.json()["content"] == "alt latest review"
    assert response.json()["structured_data"]["issues"] == ["alt issue"]
    assert response.json()["structured_data"]["quality_details"]["pacing"]["passed"] is False
    assert response.json()["structured_data"]["rule_audits"][0]["passed"] is False


def test_latest_review_api_returns_404_without_cross_branch_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="review no fallback", branches=["main", "alt"]))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="main review",
            structured_data={"chapter_number": 1},
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/reviews/latest?branch=alt&chapter_number=1")

    assert response.status_code == 404


def test_custom_rule_management_is_project_scoped_and_can_disable_enable_delete() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project_a = store.create_project(Project(idea="rules a"))
    project_b = store.create_project(Project(idea="rules b"))

    create_response = client.post(
        f"/api/projects/{project_a.project_id}/rules",
        json={"layer": "custom", "project_id": project_b.project_id, "name": "禁用词", "description": "不得出现 ForbiddenTerm"},
    )
    assert create_response.status_code == 200
    rule = create_response.json()
    assert rule["project_id"] == project_a.project_id

    cross_delete = client.delete(f"/api/projects/{project_b.project_id}/rules/{rule['rule_id']}")
    assert cross_delete.status_code == 404

    disable = client.patch(f"/api/projects/{project_a.project_id}/rules/{rule['rule_id']}", json={"enabled": False})
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False
    listed_disabled = client.get(f"/api/projects/{project_a.project_id}/rules")
    assert listed_disabled.status_code == 200
    assert listed_disabled.json()[0]["enabled"] is False

    enable = client.patch(f"/api/projects/{project_a.project_id}/rules/{rule['rule_id']}", json={"enabled": True})
    assert enable.status_code == 200
    assert enable.json()["enabled"] is True

    delete = client.delete(f"/api/projects/{project_a.project_id}/rules/{rule['rule_id']}")
    assert delete.status_code == 200
    assert client.get(f"/api/projects/{project_a.project_id}/rules").json() == []


def test_disabled_custom_rule_does_not_participate_in_review() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.generators import StoryForgeGenerators
    from storyforge.execution.rules import Rule, RuleLayer
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="disabled rule", genre="玄幻"))
    store.save_rule(Rule(layer=RuleLayer.custom, project_id=project.project_id, name="禁用词", description="不得出现 ForbiddenTerm", enabled=False))
    chapter = Asset(
        project_id=project.project_id,
        asset_type=AssetType.chapter,
        content="ForbiddenTerm appears here.",
        structured_data={"chapter_number": 1, "brief_ref": "brief", "outline_ref": "outline", "summary": "beat"},
    )

    review = StoryForgeGenerators(store).generate_review(project, chapter, chapter_number=1)

    assert review.structured_data["rule_audits"] == []
    assert "rule:禁用词" not in review.structured_data["failed_checks"]


def test_worker_drain_can_be_limited_to_one_branch() -> None:
    from storyforge.domain.models import Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch scoped drain", branches=["main", "alt"]))
    main_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="main", status="queued"))
    alt_task = store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="alt", status="queued"))

    response = client.post(f"/api/projects/{project.project_id}/workers/drain?branch=alt")

    assert response.status_code == 200
    assert response.json()["processed_task_ids"] == [alt_task.task_id]
    assert store.get_task(alt_task.task_id).status.value == "completed"
    assert store.get_task(main_task.task_id).status.value == "queued"


def test_spot_fix_worker_creates_branch_scoped_candidate_without_overwriting_official() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="spot-fix candidate", branches=["main", "alt"]))
    official = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="Opening stays.\n\nFlat middle.\n\nEnding stays.",
            structured_data={"chapter_number": 1, "title": "Alt One"},
        )
    )

    task_response = client.post(
        f"/api/projects/{project.project_id}/spot-fix",
        json={
            "chapter_asset_id": official.asset_id,
            "paragraph_indices": [1],
            "fix_instruction": "make paragraph 2 sharper",
            "branch": "alt",
        },
    )
    assert task_response.status_code == 200
    drain_response = client.post(f"/api/projects/{project.project_id}/workers/drain")
    assert drain_response.status_code == 200

    task = client.get(f"/api/tasks/{task_response.json()['task_id']}").json()
    assert task["status"] == "completed"
    candidate = store.get_asset_by_id(task["output_refs"][0])
    assert candidate is not None
    assert candidate.branch == "alt"
    assert candidate.asset_type == AssetType.chapter
    assert candidate.structured_data["is_spot_fix"] is True
    assert candidate.structured_data["original_asset_id"] == official.asset_id
    assert candidate.structured_data["spot_fix_paragraphs"] == [1]
    assert candidate.structured_data["fix_instruction"] == "make paragraph 2 sharper"

    latest_official = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt")
    assert latest_official.status_code == 200
    assert latest_official.json()["asset_id"] == official.asset_id
    assert latest_official.json()["content"] == official.content


def test_spot_fix_reject_does_not_change_latest_official_chapter() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="reject spot-fix", branches=["main", "alt"]))
    official = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="A.\n\nB.", structured_data={"chapter_number": 1})
    )
    candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="A.\n\nB fixed.",
            structured_data={
                "chapter_number": 1,
                "is_spot_fix": True,
                "original_asset_id": official.asset_id,
                "spot_fix_paragraphs": [1],
                "fix_instruction": "tighten",
            },
        )
    )

    response = client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/reject?branch=alt")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    latest = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt")
    assert latest.status_code == 200
    assert latest.json()["asset_id"] == official.asset_id
    assert latest.json()["content"] == "A.\n\nB."


def test_spot_fix_accept_creates_new_official_version_and_is_branch_scoped() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project_a = store.create_project(Project(idea="accept spot-fix", branches=["main", "alt"]))
    project_b = store.create_project(Project(idea="other project", branches=["main", "alt"]))
    official = store.save_asset(
        Asset(project_id=project_a.project_id, asset_type=AssetType.chapter, branch="alt", content="A.\n\nB.", structured_data={"chapter_number": 1})
    )
    candidate = store.save_asset(
        Asset(
            project_id=project_a.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="A.\n\nB fixed.",
            structured_data={
                "chapter_number": 1,
                "is_spot_fix": True,
                "original_asset_id": official.asset_id,
                "spot_fix_paragraphs": [1],
                "fix_instruction": "tighten",
            },
        )
    )

    cross_project = client.post(f"/api/projects/{project_b.project_id}/spot-fix/{candidate.asset_id}/accept?branch=alt")
    cross_branch = client.post(f"/api/projects/{project_a.project_id}/spot-fix/{candidate.asset_id}/accept?branch=main")
    response = client.post(f"/api/projects/{project_a.project_id}/spot-fix/{candidate.asset_id}/accept?branch=alt")

    assert cross_project.status_code == 404
    assert cross_branch.status_code == 404
    assert response.status_code == 200
    accepted = response.json()
    assert accepted["asset_id"] != candidate.asset_id
    assert accepted["branch"] == "alt"
    assert accepted["content"] == candidate.content
    assert accepted["structured_data"]["accepted_spot_fix_candidate_id"] == candidate.asset_id
    assert accepted["structured_data"]["original_asset_id"] == official.asset_id
    assert accepted["structured_data"].get("is_spot_fix") is not True

    latest = client.get(f"/api/projects/{project_a.project_id}/assets/chapter?branch=alt")
    assert latest.status_code == 200
    assert latest.json()["asset_id"] == accepted["asset_id"]
    assert latest.json()["content"] == "A.\n\nB fixed."


def test_spot_fix_diff_returns_original_and_candidate_paragraphs() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="spot-fix diff", branches=["main", "alt"]))
    official = store.save_asset(
        Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="A.\n\nB.\n\nC.", structured_data={"chapter_number": 1})
    )
    candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="A.\n\nB fixed.\n\nC.",
            structured_data={"chapter_number": 1, "is_spot_fix": True, "original_asset_id": official.asset_id, "spot_fix_paragraphs": [1], "fix_instruction": "tighten"},
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/diff?branch=alt")

    assert response.status_code == 200
    diff = response.json()
    assert diff["candidate_asset_id"] == candidate.asset_id
    assert diff["original_asset_id"] == official.asset_id
    assert diff["branch"] == "alt"
    assert diff["paragraphs"] == [{"index": 1, "original": "B.", "candidate": "B fixed."}]
    assert "-B." in diff["unified_diff"]
    assert "+B fixed." in diff["unified_diff"]


def test_spot_fix_frontend_smoke_has_paragraph_selection_diff_accept_reject_and_light_overlay() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "spot-fix-panel" in html
    assert "chapter-paragraph" in html
    assert "spotFixSelectedParagraphs" in html
    assert "spotFixDiffContent" in html
    assert "spotFixAccept" in html
    assert "spotFixReject" in html
    assert "spotFixRetry" in html
    assert "rgba(0,0,0" not in html


def test_owned_project_sensitive_endpoints_require_owner_or_editor_tokens() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project, ProjectExecutionClaim, RuntimeAuditRecord, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore
    from storyforge.execution.rules import Rule, RuleLayer
    from datetime import timedelta

    auth = TokenAuth()
    auth.register_token("owner-token", "owner-1")
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    auth.register_token("intruder-token", "intruder")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="owned sensitive endpoints",
            owner_id="owner-1",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    official = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="A.\n\nB.", structured_data={"chapter_number": 1}))
    candidate = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="A.\n\nB fixed.",
            structured_data={"chapter_number": 1, "is_spot_fix": True, "original_asset_id": official.asset_id, "spot_fix_paragraphs": [1]},
        )
    )
    rule = store.save_rule(Rule(layer=RuleLayer.custom, project_id=project.project_id, name="old", description="old"))
    store.save_project_claim(ProjectExecutionClaim(project_id=project.project_id, worker_id="worker", lease_expires_at=utc_now() + timedelta(seconds=30)))
    store.append_runtime_audit(RuntimeAuditRecord(action="claim_acquired", project_id=project.project_id, actor_worker_id="worker"))

    unauthorized = [
        client.patch(f"/api/projects/{project.project_id}", json={"title": "bad"}),
        client.patch(f"/api/projects/{project.project_id}/auto-mode", json={"auto_mode": False}),
        client.get(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/diff?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/accept?branch=main"),
        client.post(f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/reject?branch=main"),
        client.get(f"/api/projects/{project.project_id}/rules"),
        client.post(f"/api/projects/{project.project_id}/rules", json={"layer": "custom", "name": "bad", "description": "bad"}),
        client.patch(f"/api/projects/{project.project_id}/rules/{rule.rule_id}", json={"enabled": False}),
        client.delete(f"/api/projects/{project.project_id}/rules/{rule.rule_id}"),
        client.post(f"/api/projects/{project.project_id}/rules/seed"),
        client.get(f"/api/projects/{project.project_id}/runtime/claim"),
        client.delete(f"/api/projects/{project.project_id}/runtime/claim"),
        client.get(f"/api/runtime/claims?project_id={project.project_id}"),
        client.delete("/api/runtime/claims/stale"),
        client.get(f"/api/runtime/audits?project_id={project.project_id}"),
    ]
    viewer_write = client.post(
        f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/accept?branch=main",
        headers={"Authorization": "Bearer viewer-token"},
    )
    intruder_author = client.post(
        f"/api/projects/{project.project_id}/collaborators",
        headers={"Authorization": "Bearer intruder-token"},
        json={"actor_id": "new-editor", "actor_name": "New", "role": "editor"},
    )
    authorized_read = client.get(
        f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/diff?branch=main",
        headers={"Authorization": "Bearer viewer-token"},
    )
    authorized_write = client.post(
        f"/api/projects/{project.project_id}/spot-fix/{candidate.asset_id}/reject?branch=main",
        headers={"Authorization": "Bearer editor-token"},
    )
    owner_write = client.patch(
        f"/api/projects/{project.project_id}",
        headers={"Authorization": "Bearer owner-token"},
        json={"title": "good"},
    )

    assert [response.status_code for response in unauthorized] == [403] * len(unauthorized)
    assert viewer_write.status_code == 403
    assert intruder_author.status_code == 403
    assert authorized_read.status_code == 200
    assert authorized_write.status_code == 200
    assert owner_write.status_code == 200


def test_invalid_branch_name_returns_400() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="invalid branch"))

    create_response = client.post(f"/api/projects/{project.project_id}/branches", json={"branch": "bad branch/name"})
    assets_response = client.get(f"/api/projects/{project.project_id}/assets?branch=bad branch/name")

    assert create_response.status_code == 400
    assert assets_response.status_code == 400


def test_branch_scoped_api_rejects_nonexistent_branch_without_fallback() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="no fallback", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main only"))

    assets = client.get(f"/api/projects/{project.project_id}/assets?branch=ghost")
    latest = client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=ghost")
    tasks = client.get(f"/api/projects/{project.project_id}/tasks?branch=ghost")
    chapters = client.get(f"/api/projects/{project.project_id}/chapters?branch=ghost")
    export = client.post(f"/api/projects/{project.project_id}/export", json={"branch": "ghost"})
    console = client.get(f"/api/projects/{project.project_id}/production-console?branch=ghost")

    assert assets.status_code == 404
    assert latest.status_code == 404
    assert tasks.status_code == 404
    assert chapters.status_code == 404
    assert export.status_code == 404
    assert console.status_code == 404
    assert "main only" not in assets.text


def test_fork_branch_copies_only_requested_chapter_range() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="fork range", branches=["main", "third"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.brief, branch="main", content="main brief"))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="chapter 1", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="main", content="review 1", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="chapter 2", structured_data={"chapter_number": 2}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="third", content="third branch", structured_data={"chapter_number": 1}))

    response = client.post(
        f"/api/projects/{project.project_id}/branches/main/fork",
        json={"target_branch": "fork_ch1", "start_chapter": 1, "end_chapter": 1},
    )

    assert response.status_code == 200
    assert response.json()["branch"] == "fork_ch1"
    forked_assets = client.get(f"/api/projects/{project.project_id}/assets?branch=fork_ch1").json()
    assert [(asset["asset_type"], asset["content"]) for asset in forked_assets] == [("chapter", "chapter 1"), ("review_note", "review 1")]
    assert all(asset["project_id"] == project.project_id and asset["branch"] == "fork_ch1" for asset in forked_assets)


def test_queue_task_accepts_branch_and_rejects_missing_branch() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="queue branch task", branches=["main", "alt"]))

    response = client.post(
        f"/api/projects/{project.project_id}/tasks",
        json={"task_type": "brief_generation", "payload": {"mode": "alt"}, "branch": "alt"},
    )
    missing = client.post(
        f"/api/projects/{project.project_id}/tasks",
        json={"task_type": "brief_generation", "branch": "ghost"},
    )

    assert response.status_code == 200
    assert response.json()["branch"] == "alt"
    assert response.json()["payload"] == {"mode": "alt"}
    assert missing.status_code == 404


def test_fresh_fork_compares_unchanged_when_only_lineage_metadata_differs() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="clean fork compare"))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="same chapter",
            structured_data={"chapter_number": 1, "title": "Same"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.review_note,
            branch="main",
            content="same review",
            structured_data={"chapter_number": 1, "approved": True, "failed_checks": []},
        )
    )
    fork = client.post(f"/api/projects/{project.project_id}/branches/main/fork", json={"target_branch": "copy"})
    assert fork.status_code == 200

    compare = client.get(f"/api/projects/{project.project_id}/branches/compare?left=main&right=copy")

    assert compare.status_code == 200
    assert compare.json()["chapter_diffs"][0]["status"] == "unchanged"
    assert compare.json()["audit_diffs"][0]["status"] == "unchanged"


def test_compare_branch_audits_detects_review_content_changes() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="review content compare", branches=["main", "alt"]))
    shared_review_data = {"chapter_number": 1, "approved": True, "failed_checks": []}
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="main", content="same verdict, main notes", structured_data=shared_review_data))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="alt", content="same verdict, alt notes", structured_data=shared_review_data))

    response = client.get(f"/api/projects/{project.project_id}/branches/compare?left=main&right=alt")

    assert response.status_code == 200
    assert response.json()["audit_diffs"][0]["status"] == "changed"


def test_next_chapter_uses_branch_scoped_latest_chapter() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="branch cursor", branches=["main", "alt"]))
    project.latest_chapter_cursor = 10
    store.save_project(project)
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.brief,
            branch="alt",
            content="brief",
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.outline,
            branch="alt",
            content="outline",
            structured_data={"chapters": [{"chapter_number": 1}, {"chapter_number": 2}]},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="alt c1",
            structured_data={"chapter_number": 1},
        )
    )

    response = client.post(f"/api/projects/{project.project_id}/runs/next-chapter", json={"branch": "alt"})

    assert response.status_code == 200
    tasks = response.json()
    assert [task["payload"]["chapter_number"] for task in tasks] == [2, 2]
    assert [task["task_type"] for task in tasks] == ["chapter_generation", "chapter_review"]
    assert all(task["branch"] == "alt" for task in tasks)


def test_compare_branches_uses_only_left_and_right_branches() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="compare", branches=["main", "alt", "third"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main c1", structured_data={"chapter_number": 1, "title": "Main"}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt c1", structured_data={"chapter_number": 1, "title": "Alt"}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="third", content="third c1", structured_data={"chapter_number": 1, "title": "Third"}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="main", content="main audit", structured_data={"chapter_number": 1, "approved": True}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="alt", content="alt audit", structured_data={"chapter_number": 1, "approved": False, "issues": ["alt issue"]}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="third", content="third audit", structured_data={"chapter_number": 1, "issues": ["third issue"]}))

    response = client.get(f"/api/projects/{project.project_id}/branches/compare?left=main&right=alt")

    assert response.status_code == 200
    data = response.json()
    assert data["left"] == "main"
    assert data["right"] == "alt"
    assert data["chapter_diffs"] == [{"chapter_number": 1, "left_asset_id": data["chapter_diffs"][0]["left_asset_id"], "right_asset_id": data["chapter_diffs"][0]["right_asset_id"], "left_title": "Main", "right_title": "Alt", "status": "changed"}]
    assert data["audit_diffs"][0]["left_approved"] is True
    assert data["audit_diffs"][0]["right_approved"] is False
    serialized = json.dumps(data)
    assert "third" not in serialized
    assert "third issue" not in serialized


def test_viewer_cannot_create_or_fork_branches() -> None:
    from storyforge.domain.models import CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(
        Project(
            idea="viewer branch permissions",
            collaborators=[CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer")],
        )
    )

    create_response = client.post(
        f"/api/projects/{project.project_id}/branches?actor_id=viewer-amy",
        json={"branch": "viewer_branch"},
    )
    fork_response = client.post(
        f"/api/projects/{project.project_id}/branches/main/fork?actor_id=viewer-amy",
        json={"target_branch": "viewer_fork"},
    )

    assert create_response.status_code == 403
    assert fork_response.status_code == 403
    assert store.get_project(project.project_id).branches == ["main"]


def test_branch_mutations_require_authorized_non_viewer_token() -> None:
    from storyforge.domain.models import CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="branch token permissions",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )

    impersonated = client.post(f"/api/projects/{project.project_id}/branches?actor_id=editor-eva", json={"branch": "impersonated"})
    viewer = client.post(
        f"/api/projects/{project.project_id}/branches",
        headers={"Authorization": "Bearer viewer-token"},
        json={"branch": "viewer_branch"},
    )
    editor = client.post(
        f"/api/projects/{project.project_id}/branches",
        headers={"Authorization": "Bearer editor-token"},
        json={"branch": "editor_branch"},
    )
    fork = client.post(
        f"/api/projects/{project.project_id}/branches/main/fork",
        headers={"Authorization": "Bearer editor-token"},
        json={"target_branch": "editor_fork"},
    )

    assert impersonated.status_code == 403
    assert viewer.status_code == 403
    assert editor.status_code == 200
    assert fork.status_code == 200


def test_branch_read_endpoints_require_authorized_token_when_collaborators_exist() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project, TaskRecord, TaskType
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="branch read permissions",
            branches=["main", "alt"],
            collaborators=[CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor")],
        )
    )
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", content="main", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.review_note, branch="alt", content="review", structured_data={"chapter_number": 1}))
    first_alt_chapter = store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", content="alt v1", structured_data={"chapter_number": 1}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="alt v2",
            structured_data={"chapter_number": 1, "origin_asset_id": first_alt_chapter.asset_id},
        ),
        lineage_origin_id=first_alt_chapter.asset_id,
    )
    store.create_task(TaskRecord(project_id=project.project_id, task_type=TaskType.brief_generation, branch="alt"))

    unauthorized_responses = [
        client.get(f"/api/projects/{project.project_id}/branches"),
        client.get(f"/api/projects/{project.project_id}/branches?actor_id=editor-eva"),
        client.get(f"/api/projects/{project.project_id}/branches/compare?left=main&right=alt"),
        client.get(f"/api/projects/{project.project_id}/production-console?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/assets?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/reviews/latest?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=alt"),
        client.post(f"/api/projects/{project.project_id}/assets/chapter/diff?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/chapters?branch=alt"),
        client.get(f"/api/projects/{project.project_id}/tasks?branch=alt"),
        client.post(f"/api/projects/{project.project_id}/export", json={"branch": "alt"}),
        client.get(f"/api/projects/{project.project_id}/style-profile?branch=alt"),
    ]
    authorized_responses = [
        client.get(f"/api/projects/{project.project_id}/branches", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/branches/compare?left=main&right=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/production-console?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/assets?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/assets/chapter?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/reviews/latest?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/assets/chapter/versions?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.post(f"/api/projects/{project.project_id}/assets/chapter/diff?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/chapters?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.get(f"/api/projects/{project.project_id}/tasks?branch=alt", headers={"Authorization": "Bearer editor-token"}),
        client.post(f"/api/projects/{project.project_id}/export", headers={"Authorization": "Bearer editor-token"}, json={"branch": "alt"}),
        client.get(f"/api/projects/{project.project_id}/style-profile?branch=alt", headers={"Authorization": "Bearer editor-token"}),
    ]

    assert [response.status_code for response in unauthorized_responses] == [403] * len(unauthorized_responses)
    assert [response.status_code for response in authorized_responses] == [200] * len(authorized_responses)


def test_style_profile_put_locks_branch_scoped_overrides() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="style profile", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", source="human", content="He smelled rust and smoke.", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="Cold rain traced her breath.", structured_data={"chapter_number": 1}))

    saved = client.put(
        f"/api/projects/{project.project_id}/style-profile",
        json={
            "branch": "alt",
            "voice": "locked close third",
            "strengths": ["slow dread"],
            "avoid": ["flat exposition"],
            "sensory_keywords": ["ash", "rain"],
            "locked_fields": ["voice", "avoid", "sensory_keywords"],
        },
    )
    alt_profile = client.get(f"/api/projects/{project.project_id}/style-profile?branch=alt")
    main_profile = client.get(f"/api/projects/{project.project_id}/style-profile?branch=main")

    assert saved.status_code == 200
    assert saved.json()["branch"] == "alt"
    assert saved.json()["locked_fields"] == ["voice", "avoid", "sensory_keywords"]
    assert alt_profile.status_code == 200
    assert alt_profile.json()["voice"] == "locked close third"
    assert alt_profile.json()["avoid"] == ["flat exposition"]
    assert alt_profile.json()["sensory_keywords"] == ["ash", "rain"]
    assert alt_profile.json()["strengths"] == ["sensory detail"]
    assert alt_profile.json()["sample_count"] == 1
    assert main_profile.json()["voice"] != "locked close third"
    assert "rust" in main_profile.json()["sensory_keywords"]


def test_style_profile_get_defaults_to_main_branch() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="style default", branches=["main", "alt"]))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="main", source="human", content="He smelled rust and smoke.", structured_data={"chapter_number": 1}))
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, branch="alt", source="human", content="Cold rain traced her breath.", structured_data={"chapter_number": 1}))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.rules,
            branch="alt",
            structured_data={
                "kind": "style_profile",
                "voice": "alt locked voice",
                "sensory_keywords": ["ash"],
                "locked_fields": ["voice", "sensory_keywords"],
            },
        )
    )

    response = client.get(f"/api/projects/{project.project_id}/style-profile")

    assert response.status_code == 200
    assert response.json()["voice"] != "alt locked voice"
    assert "rust" in response.json()["sensory_keywords"]
    assert "ash" not in response.json()["sensory_keywords"]


def test_style_profile_put_requires_editor_access() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="style auth",
            owner_id="owner-olga",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    store.save_asset(Asset(project_id=project.project_id, asset_type=AssetType.chapter, source="human", content="rain", structured_data={"chapter_number": 1}))
    body = {"branch": "main", "voice": "editor voice", "locked_fields": ["voice"]}

    unauthorized = client.put(f"/api/projects/{project.project_id}/style-profile", json=body)
    viewer = client.put(f"/api/projects/{project.project_id}/style-profile", headers={"Authorization": "Bearer viewer-token"}, json=body)
    editor = client.put(f"/api/projects/{project.project_id}/style-profile", headers={"Authorization": "Bearer editor-token"}, json=body)

    assert unauthorized.status_code == 403
    assert viewer.status_code == 403
    assert editor.status_code == 200


def test_trash_and_restore_require_read_write_authorized_tokens() -> None:
    from storyforge.domain.models import Asset, AssetType, CollaboratorRecord, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    auth = TokenAuth()
    auth.register_token("editor-token", "editor-eva")
    auth.register_token("viewer-token", "viewer-amy")
    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store, auth=auth))
    project = store.create_project(
        Project(
            idea="trash token permissions",
            collaborators=[
                CollaboratorRecord(actor_id="editor-eva", actor_name="Eva", role="editor"),
                CollaboratorRecord(actor_id="viewer-amy", actor_name="Amy", role="viewer"),
            ],
        )
    )
    asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="deleted chapter",
            structured_data={"chapter_number": 1},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )

    trash_without_token = client.get(f"/api/projects/{project.project_id}/assets/trash")
    trash_query_impersonation = client.get(f"/api/projects/{project.project_id}/assets/trash?actor_id=viewer-amy")
    trash_with_viewer = client.get(
        f"/api/projects/{project.project_id}/assets/trash",
        headers={"Authorization": "Bearer viewer-token"},
    )
    restore_with_viewer = client.post(
        f"/api/projects/{project.project_id}/assets/{asset.asset_id}/restore",
        headers={"Authorization": "Bearer viewer-token"},
    )
    restore_with_editor = client.post(
        f"/api/projects/{project.project_id}/assets/{asset.asset_id}/restore",
        headers={"Authorization": "Bearer editor-token"},
    )

    assert trash_without_token.status_code == 403
    assert trash_query_impersonation.status_code == 403
    assert trash_with_viewer.status_code == 200
    assert restore_with_viewer.status_code == 403
    assert restore_with_editor.status_code == 200


def test_fork_branch_skips_deleted_foreshadowing_and_does_not_copy_comments() -> None:
    from storyforge.domain.models import Asset, AssetType, Project, utc_now
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="safe branch fork"))
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="live chapter",
            structured_data={"chapter_number": 1},
            comments=[{"comment_id": "c1", "content": "private note"}],
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="deleted chapter",
            structured_data={"chapter_number": 2},
            is_deleted=True,
            deleted_at=utc_now(),
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.continuity_note,
            branch="main",
            content="fork bypass",
            structured_data={"kind": "foreshadowing", "title": "Fork bypass", "introduced_chapter": 1},
        )
    )

    response = client.post(f"/api/projects/{project.project_id}/branches/main/fork", json={"target_branch": "clean_fork"})

    assert response.status_code == 200
    forked_assets = client.get(f"/api/projects/{project.project_id}/assets?branch=clean_fork").json()
    assert [(asset["asset_type"], asset["content"]) for asset in forked_assets] == [("chapter", "live chapter")]
    assert forked_assets[0]["comments"] == []
    assert forked_assets[0]["is_deleted"] is False
    assert forked_assets[0]["deleted_at"] is None


def test_branch_frontend_smoke_has_fork_compare_status_and_light_background() -> None:
    client = TestClient(create_app(store=InMemoryStoryForgeStore()))

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "branch-status-badge" in html
    assert "fork-branch-btn" in html
    assert "compare-branches-btn" in html
    assert "branch-compare-card" in html
    assert "branch-chapter-diff" in html
    assert "branch-audit-diff" in html
    assert "foreshadowing-card" in html
    assert "foreshadowing-list" in html
    assert "foreshadowing-form" in html
    assert "timeline-card" in html
    assert "timeline-list" in html
    assert "timeline-form" in html
    assert "pacing-card" in html
    assert "pacing-curve-svg" in html
    assert "style-card" in html
    assert "style-profile-form" in html
    assert "style-voice-input" in html
    assert "style-locked-fields-input" in html
    assert "comment-anchor-type-input" in html
    assert "comment-paragraph-index-input" in html
    assert "comment-status-filter" in html
    assert "current-role-input" in html
    assert "approval-status-input" in html
    assert "resolveComment" in html
    assert "JSON.stringify({ content, source, branch: state.selectedBranch, base_version: existing.version, actor_id: actorId, actor_name: actorName, role, approval_status: approvalStatus, structured_data: structuredData })" in html
    assert "JSON.stringify({ target_version: targetVersion, branch: state.selectedBranch })" in html
    assert "DELETE" in html and "?branch=${encodeURIComponent(state.selectedBranch)}" in html
    assert 'addEventListener("asset_updated", onEventReceived)' in html
    assert "await refreshSelectedProject(); startEventStream();" in html
    assert 'background: var(--bg)' in html
    assert "background: #000" not in html
    assert "background: #000000" not in html


def test_timeline_crud_is_branch_scoped_and_ordered() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="timeline", branches=["main", "alt"]))

    second = client.post(
        f"/api/projects/{project.project_id}/timeline",
        json={
            "branch": "alt",
            "chapter_number": 2,
            "title": "Aftermath",
            "description": "The city reacts.",
            "story_time_label": "Day 2",
            "order_index": 20,
            "characters": ["Mira"],
            "location": "Capital",
        },
    )
    first = client.post(
        f"/api/projects/{project.project_id}/timeline",
        json={
            "branch": "alt",
            "chapter_number": 1,
            "title": "Inciting incident",
            "description": "The gate opens.",
            "story_time_label": "Day 1",
            "order_index": 10,
            "characters": ["Mira", "Ren"],
            "location": "Harbor",
        },
    )
    main_list = client.get(f"/api/projects/{project.project_id}/timeline?branch=main")
    alt_list = client.get(f"/api/projects/{project.project_id}/timeline?branch=alt")

    assert second.status_code == 200
    assert first.status_code == 200
    first_id = first.json()["timeline_event_id"]
    assert main_list.json()["items"] == []
    assert [item["title"] for item in alt_list.json()["items"]] == ["Inciting incident", "Aftermath"]

    patched = client.patch(
        f"/api/projects/{project.project_id}/timeline/{first_id}",
        json={"branch": "alt", "chapter_number": 3, "location": "Old Harbor", "order_index": 30},
    )
    reordered = client.get(f"/api/projects/{project.project_id}/timeline?branch=alt")
    deleted = client.delete(f"/api/projects/{project.project_id}/timeline/{first_id}?branch=alt")
    final_alt = client.get(f"/api/projects/{project.project_id}/timeline?branch=alt")
    asset_events = [event for event in store.list_events("") if event.event_type.value == "asset_updated"]

    assert patched.status_code == 200
    assert patched.json()["location"] == "Old Harbor"
    assert [item["title"] for item in reordered.json()["items"]] == ["Aftermath", "Inciting incident"]
    assert deleted.status_code == 200
    assert [item["timeline_event_id"] for item in final_alt.json()["items"]] == [second.json()["timeline_event_id"]]
    assert {event.payload.get("branch") for event in asset_events} == {"alt"}


def test_foreshadowing_crud_is_branch_scoped() -> None:
    from storyforge.domain.models import Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="foreshadowing", branches=["main", "alt"]))

    created = client.post(
        f"/api/projects/{project.project_id}/foreshadowing",
        json={
            "branch": "alt",
            "title": "Locked door",
            "description": "A sealed cellar door appears in chapter one.",
            "introduced_chapter": 1,
            "expected_resolution_chapter": 3,
        },
    )
    main_list = client.get(f"/api/projects/{project.project_id}/foreshadowing?branch=main")
    alt_list = client.get(f"/api/projects/{project.project_id}/foreshadowing?branch=alt")

    assert created.status_code == 200
    record_id = created.json()["foreshadowing_id"]
    assert main_list.json()["items"] == []
    assert [item["foreshadowing_id"] for item in alt_list.json()["items"]] == [record_id]

    patched = client.patch(
        f"/api/projects/{project.project_id}/foreshadowing/{record_id}",
        json={"branch": "alt", "status": "resolved", "resolved_chapter": 3},
    )
    deleted = client.delete(f"/api/projects/{project.project_id}/foreshadowing/{record_id}?branch=alt")
    final_alt = client.get(f"/api/projects/{project.project_id}/foreshadowing?branch=alt")

    assert patched.status_code == 200
    assert patched.json()["status"] == "resolved"
    assert patched.json()["resolved_chapter"] == 3
    assert deleted.status_code == 200
    assert final_alt.json()["items"] == []


def test_generic_asset_mutations_cannot_modify_foreshadowing_records() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore

    store = InMemoryStoryForgeStore()
    client = TestClient(create_app(store=store))
    project = store.create_project(Project(idea="foreshadowing generic guard", branches=["main", "alt"]))
    legacy_foreshadowing = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.continuity_note,
            branch="main",
            content="legacy foreshadowing",
            structured_data={"kind": "foreshadowing", "title": "Legacy clue", "introduced_chapter": 1},
        )
    )
    generic_create = client.post(
        f"/api/projects/{project.project_id}/assets",
        json={
            "asset_type": "continuity_note",
            "content": "created through generic API",
            "structured_data": {"kind": "foreshadowing", "title": "Created bypass", "introduced_chapter": 1},
        },
    )
    normal_note = client.post(
        f"/api/projects/{project.project_id}/assets",
        json={"asset_type": "continuity_note", "content": "normal note", "structured_data": {"kind": "note"}},
    )
    created = client.post(
        f"/api/projects/{project.project_id}/foreshadowing",
        json={
            "branch": "alt",
            "title": "Guarded clue",
            "description": "Must use dedicated API.",
            "introduced_chapter": 1,
        },
    )
    normal_note_id = normal_note.json()["asset_id"]
    record_id = created.json()["foreshadowing_id"]

    generic_kind_update = client.put(
        f"/api/projects/{project.project_id}/assets/{normal_note_id}",
        json={"content": "converted through generic API", "structured_data": {"kind": "foreshadowing", "title": "Converted"}},
    )
    generic_kind_rollback = client.post(
        f"/api/projects/{project.project_id}/assets/{normal_note_id}/rollback",
        json={"target_version": legacy_foreshadowing.version},
    )
    generic_update = client.put(
        f"/api/projects/{project.project_id}/assets/{record_id}",
        json={"content": "changed through generic API", "structured_data": {"title": "Bypassed"}},
    )
    generic_delete = client.delete(f"/api/projects/{project.project_id}/assets/{record_id}")
    generic_rollback = client.post(
        f"/api/projects/{project.project_id}/assets/{record_id}/rollback",
        json={"target_version": 1},
    )
    dedicated_update = client.patch(
        f"/api/projects/{project.project_id}/foreshadowing/{record_id}",
        json={"branch": "alt", "title": "Dedicated update"},
    )
    dedicated_delete = client.delete(f"/api/projects/{project.project_id}/foreshadowing/{record_id}?branch=alt")
    generic_restore = client.post(f"/api/projects/{project.project_id}/assets/{record_id}/restore")
    generic_comment = client.post(
        f"/api/projects/{project.project_id}/assets/{record_id}/comments",
        json={"author": "Bypass", "content": "changed through generic comments"},
    )

    assert generic_create.status_code == 404
    assert generic_kind_update.status_code == 404
    assert generic_kind_rollback.status_code == 404
    assert generic_update.status_code == 404
    assert generic_delete.status_code == 404
    assert generic_rollback.status_code == 404
    assert generic_comment.status_code == 404
    assert dedicated_update.status_code == 200
    assert dedicated_update.json()["title"] == "Dedicated update"
    assert dedicated_delete.status_code == 200
    assert generic_restore.status_code == 404


def test_review_uses_only_current_branch_foreshadowing_overdue_items() -> None:
    from storyforge.domain.models import Asset, AssetType, Project
    from storyforge.execution.store import InMemoryStoryForgeStore
    from storyforge.execution.generators import StoryForgeGenerators

    store = InMemoryStoryForgeStore()
    project = store.create_project(Project(idea="branch foreshadowing", branches=["main", "alt"]))
    main_chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="main",
            content="main chapter",
            structured_data={"chapter_number": 4, "outline_ref": "outline", "brief_ref": "brief", "summary": "beat"},
        )
    )
    alt_chapter = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            branch="alt",
            content="alt chapter",
            structured_data={"chapter_number": 4, "outline_ref": "outline", "brief_ref": "brief", "summary": "beat"},
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.continuity_note,
            branch="main",
            content="Main overdue",
            structured_data={
                "kind": "foreshadowing",
                "title": "Main clue",
                "status": "planted",
                "introduced_chapter": 1,
                "expected_resolution_chapter": 3,
            },
        )
    )
    store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.continuity_note,
            branch="alt",
            content="Alt resolved",
            structured_data={
                "kind": "foreshadowing",
                "title": "Alt clue",
                "status": "resolved",
                "introduced_chapter": 1,
                "expected_resolution_chapter": 3,
                "resolved_chapter": 3,
            },
        )
    )

    generator = StoryForgeGenerators(store)
    main_review = generator.generate_review(project, main_chapter, 4)
    alt_review = generator.generate_review(project, alt_chapter, 4)

    assert "foreshadowing:overdue" in main_review.structured_data["failed_checks"]
    assert "Foreshadowing overdue: Main clue" in main_review.structured_data["issues"]
    assert "foreshadowing:overdue" not in alt_review.structured_data["failed_checks"]
    assert "Main clue" not in " ".join(alt_review.structured_data["issues"])
