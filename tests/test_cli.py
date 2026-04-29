"""Tests for CLI commands."""

from unittest.mock import call, patch, MagicMock

import httpx
import pytest

from storyforge.cli.main import cli


def _mock_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {}
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    return response


def test_create_command_success():
    mock_resp = _mock_response(json_data={"project_id": "proj_abc123", "idea": "test"})
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.post.return_value = mock_resp
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["create", "--idea", "test"], standalone_mode=False)
            echo.assert_called_with("已创建项目：proj_abc123")


def test_create_command_connection_error():
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.post.side_effect = httpx.ConnectError("refused")
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(SystemExit) as exc_info:
            cli(["create", "--idea", "test"], standalone_mode=False)
        assert exc_info.value.code == 1


def test_start_command_lists_tasks():
    mock_resp = _mock_response(json_data=[
        {"task_id": "task_1", "task_type": "brief_generation", "status": "queued"},
        {"task_id": "task_2", "task_type": "outline_generation", "status": "queued"},
    ])
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.post.return_value = mock_resp
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["start", "proj_abc", "--run"], standalone_mode=False)
            assert echo.call_count == 3  # queued message + 2 tasks


def test_status_command_shows_summary():
    summary_resp = _mock_response(json_data={
        "project_id": "proj_abc",
        "task_summary": {"total": 10, "completed": 10, "queued": 0, "running": 0, "failed": 0, "waiting_retry": 0, "accepted": 0, "cancelled": 0},
        "latest_event_message": "Task completed",
        "latest_event_type": "completed",
        "failure_summary": {"failed_task_ids": [], "waiting_retry_task_ids": [], "last_error_by_task": {}},
    })
    critical_resp = _mock_response(json_data={
        "critical_path": [
            {"stage": "brief", "status": "completed", "blocked_reason": ""},
            {"stage": "export_candidate", "status": "completed", "blocked_reason": ""},
        ],
        "export_ready": True,
        "export_blocked_reason": "",
    })
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.get.side_effect = [summary_resp, critical_resp]
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["status", "proj_abc"], standalone_mode=False)
            assert any("completed" in str(call) for call in echo.call_args_list)
            assert any("Critical path" in str(call) for call in echo.call_args_list)


def test_chapters_command_shows_list():
    mock_resp = _mock_response(json_data=[
        {"chapter_number": 1, "title": "Chapter One", "status": "completed", "asset_id": "asset_1", "review_approved": True},
        {"chapter_number": 2, "title": "Chapter Two", "status": "pending", "asset_id": None, "review_approved": None},
    ])
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["chapters", "proj_abc"], standalone_mode=False)
            assert echo.call_count == 2


def test_drain_command_shows_processed_count():
    mock_resp = _mock_response(json_data={"processed_task_ids": ["t1", "t2"], "processed_count": 2})
    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.post.return_value = mock_resp
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["drain", "proj_abc"], standalone_mode=False)
            echo.assert_called_with("Processed 2 tasks")


def test_read_command_outputs_requested_chapter_content():
    chapters_resp = _mock_response(json_data=[
        {"chapter_number": 1, "title": "Opening", "status": "completed", "asset_id": "asset_1", "review_approved": True},
        {"chapter_number": 2, "title": "Debt", "status": "completed", "asset_id": "asset_2", "review_approved": True},
    ])
    versions_resp = _mock_response(json_data=[
        {"asset_id": "asset_1", "content": "Chapter one body", "structured_data": {"chapter_number": 1, "title": "Opening"}},
        {"asset_id": "asset_2", "content": "Chapter two body", "structured_data": {"chapter_number": 2, "title": "Debt"}},
    ])

    def get_side_effect(url):
        if url == "/api/projects/proj_abc/chapters":
            return chapters_resp
        if url == "/api/projects/proj_abc/assets/final_chapter/versions":
            return versions_resp
        raise AssertionError(f"unexpected GET {url}")

    with patch("storyforge.cli.main._client") as mock_client_ctx:
        ctx = MagicMock()
        ctx.get.side_effect = get_side_effect
        mock_client_ctx.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("click.echo") as echo:
            cli(["read", "proj_abc", "2"], standalone_mode=False)
            echo.assert_called_with("Chapter two body")
            assert ctx.get.call_args_list == [
                call("/api/projects/proj_abc/chapters"),
                call("/api/projects/proj_abc/assets/final_chapter/versions"),
            ]



def test_serve_command_starts_uvicorn():
    """Verify serve command invokes uvicorn.run with expected defaults."""
    with patch("uvicorn.run") as mock_run, patch("storyforge.cli.main.threading") as mock_threading:
        cli(["serve"], standalone_mode=False)
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == "storyforge.api.app:app"
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["host"] == "127.0.0.1"
        assert call_kwargs["port"] == 8000
        mock_threading.Timer.return_value.start.assert_called_once()


def test_serve_command_opens_browser():
    """Verify serve command attempts to open the browser after a short delay."""
    with patch("uvicorn.run"), patch("storyforge.cli.main.threading") as mock_threading, patch("storyforge.cli.main.webbrowser") as mock_webbrowser:
        cli(["serve"], standalone_mode=False)
        mock_threading.Timer.assert_called_once()
        timer_args = mock_threading.Timer.call_args
        assert timer_args[0][0] == 1.5  # 1.5 second delay
        mock_threading.Timer.return_value.start.assert_called_once()
        # Simulate the timer callback
        timer_args[0][1]()
        mock_webbrowser.open.assert_called_once_with("http://localhost:8000", new=2)


def test_serve_command_no_browser_skips_timer():
    with patch("uvicorn.run"), patch("storyforge.cli.main.threading") as mock_threading, patch("storyforge.cli.main.webbrowser") as mock_webbrowser:
        cli(["serve", "--no-browser"], standalone_mode=False)
        mock_threading.Timer.assert_not_called()
        mock_webbrowser.open.assert_not_called()
