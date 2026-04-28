from __future__ import annotations

import sys
import threading
import webbrowser

import click
import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


def _client() -> httpx.Client:
    return httpx.Client(base_url=DEFAULT_BASE_URL, timeout=30.0)


@click.group()
@click.version_option(version="0.1.0", prog_name="storyforge")
def cli() -> None:
    """StoryForge CLI - create, run, and read AI-generated novels."""


@cli.command()
@click.option("--idea", required=True, help="Creative seed for the project")
@click.option("--title", default="", help="Project title (auto-generated if empty)")
@click.option("--genre", default="", help="Genre (e.g. progression fantasy)")
@click.option("--target-length", default=0, type=int, help="Target number of chapters")
def create(idea: str, title: str, genre: str, target_length: int) -> None:
    """Create a new project."""
    payload = {
        "idea": idea,
        "title": title,
        "genre": genre,
        "target_length": target_length,
    }
    try:
        with _client() as client:
            resp = client.post("/api/projects", json=payload)
            resp.raise_for_status()
            project = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API. Is it running at http://localhost:8000?")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    click.echo(f"Created project: {project['project_id']}")


@cli.command()
@click.argument("project_id")
@click.option("--chapter", default=1, type=int, help="Starting chapter number")
@click.option("--run", is_flag=True, help="Auto-run the pipeline (drain immediately)")
def start(project_id: str, chapter: int, run: bool) -> None:
    """Start the first loop (brief -> outline -> chapter -> review)."""
    payload = {"chapter_number": chapter, "auto_run": run}
    try:
        with _client() as client:
            resp = client.post(f"/api/projects/{project_id}/runs/first-loop", json=payload)
            resp.raise_for_status()
            tasks = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API. Is it running at http://localhost:8000?")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    click.echo(f"Queued {len(tasks)} tasks for first loop:")
    for task in tasks:
        click.echo(f"  [{task['task_id']}] {task['task_type']} (status: {task['status']})")


@cli.command("status")
@click.argument("project_id")
def project_status(project_id: str) -> None:
    """Show project execution summary."""
    try:
        with _client() as client:
            resp = client.get(f"/api/projects/{project_id}/summary")
            resp.raise_for_status()
            summary = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    ts = summary["task_summary"]
    click.echo(f"Project: {summary['project_id']}")
    click.echo(f"Total tasks: {ts['total']}")
    click.echo(f"  completed: {ts['completed']}")
    click.echo(f"  queued: {ts['queued']}")
    click.echo(f"  running: {ts['running']}")
    click.echo(f"  failed: {ts['failed']}")
    click.echo(f"  waiting_retry: {ts['waiting_retry']}")

    if summary["latest_event_message"]:
        click.echo(f"Latest event: {summary['latest_event_type']} - {summary['latest_event_message']}")

    fs = summary.get("failure_summary", {})
    if fs.get("failed_task_ids"):
        click.echo("Failed tasks:")
        for tid in fs["failed_task_ids"]:
            click.echo(f"  {tid} - {fs['last_error_by_task'].get(tid, 'unknown')}")


@cli.command()
@click.argument("project_id")
def chapters(project_id: str) -> None:
    """List all chapters for a project."""
    try:
        with _client() as client:
            resp = client.get(f"/api/projects/{project_id}/chapters")
            resp.raise_for_status()
            chapter_list = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    if not chapter_list:
        click.echo("No chapters yet. Run 'storyforge start <project_id>' to begin.")
        return

    for ch in chapter_list:
        icon = "OK" if ch.get("review_approved") else "  "
        click.echo(f"  [{icon}] Ch{ch['chapter_number']}: {ch.get('title', '(untitled)')} [{ch['status']}]")


@cli.command()
@click.argument("project_id")
@click.option("--target", default=None, type=int, help="Target chapter number (defaults to outline end)")
@click.option("--run", is_flag=True, help="Auto-run after queuing")
def loop(project_id: str, target: int | None, run: bool) -> None:
    """Queue chapters from current cursor to target (or outline end)."""
    payload: dict = {"auto_run": run}
    if target is not None:
        payload["target_chapter"] = target

    try:
        with _client() as client:
            resp = client.post(f"/api/projects/{project_id}/runs/chapter-loop", json=payload)
            resp.raise_for_status()
            tasks = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    click.echo(f"Queued {len(tasks)} tasks for chapter loop")


@cli.command()
@click.argument("project_id")
def drain(project_id: str) -> None:
    """Process all queued tasks now."""
    try:
        with _client() as client:
            resp = client.post(f"/api/projects/{project_id}/workers/drain")
            resp.raise_for_status()
            result = resp.json()
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)

    click.echo(f"Processed {result['processed_count']} tasks")


@cli.command()
@click.argument("project_id")
@click.argument("chapter_number", type=int)
def read(project_id: str, chapter_number: int) -> None:
    """Read a chapter's full text."""
    try:
        with _client() as client:
            # Get chapters list to find the asset_id
            resp = client.get(f"/api/projects/{project_id}/chapters")
            resp.raise_for_status()
            chapter_list = resp.json()

            chapter_info = next((ch for ch in chapter_list if ch["chapter_number"] == chapter_number), None)
            if chapter_info is None:
                click.echo(f"Chapter {chapter_number} not found")
                sys.exit(1)

            asset_id = chapter_info.get("asset_id")
            if asset_id is None:
                click.echo(f"Chapter {chapter_number} has not been generated yet")
                sys.exit(1)

            versions_resp = client.get(f"/api/projects/{project_id}/assets/chapter/versions")
            versions_resp.raise_for_status()
            assets = versions_resp.json()

            for asset in assets:
                if asset["asset_id"] == asset_id:
                    click.echo(asset["content"])
                    return

            click.echo(f"Chapter asset {asset_id} not found")
            sys.exit(1)
    except httpx.ConnectError:
        click.echo("Error: cannot connect to StoryForge API.")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8000, type=int, help="Port number")
@click.option("--no-browser", is_flag=True, help="Do not open the browser automatically")
def serve(host: str, port: int, no_browser: bool) -> None:
    """Start the StoryForge API server and open the workbench in a browser."""

    if not no_browser:
        def _open_browser() -> None:
            webbrowser.open(f"http://localhost:{port}", new=2)

        timer = threading.Timer(1.5, _open_browser)
        timer.daemon = True
        timer.start()

    click.echo(f"Starting StoryForge at http://localhost:{port}")
    import uvicorn
    uvicorn.run("storyforge.api.app:app", host=host, port=port, log_level="info")
