from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from storyforge.domain.models import Asset, AssetType, EventRecord, Project, ProjectExecutionClaim, RuntimeAuditRecord, TaskRecord
from storyforge.execution.rules import Rule, RuleLayer
from storyforge.execution.store import StoryForgeStore


class SQLiteStoryForgeStore(StoryForgeStore):
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assets_project_type_version
                    ON assets(project_id, asset_type, version);

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_task_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_project_created
                    ON tasks(project_id, created_at);

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task_timestamp
                    ON events(task_id, timestamp);

                CREATE TABLE IF NOT EXISTS project_claims (
                    project_id TEXT PRIMARY KEY,
                    lease_expires_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_audits (
                    audit_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_audits_project_created
                    ON runtime_audits(project_id, created_at);

                CREATE TABLE IF NOT EXISTS rules (
                    rule_id TEXT PRIMARY KEY,
                    layer TEXT NOT NULL,
                    genre TEXT,
                    project_id TEXT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_rules_layer_genre
                    ON rules(layer, genre);
                """
            )
            self.conn.commit()

    def create_project(self, project: Project) -> Project:
        return self.save_project(project)

    def save_project(self, project: Project) -> Project:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO projects (project_id, created_at, updated_at, data)
                VALUES (?, ?, ?, ?)
                """,
                (project.project_id, project.created_at.isoformat(), project.updated_at.isoformat(), project.model_dump_json()),
            )
            self.conn.commit()
        return project

    def get_project(self, project_id: str) -> Project | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        return Project.model_validate_json(row["data"])

    def list_projects(self) -> list[Project]:
        with self._lock:
            rows = self.conn.execute("SELECT data FROM projects ORDER BY created_at ASC").fetchall()
        return [Project.model_validate_json(row["data"]) for row in rows]

    def save_asset(self, asset: Asset, *, expected_version: int | None = None, lineage_origin_id: str | None = None) -> Asset:
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS current_version FROM assets WHERE project_id = ? AND asset_type = ?",
                (asset.project_id, asset.asset_type.value),
            ).fetchone()
            current_version = int(row["current_version"])
            latest_version = current_version
            if expected_version is not None and lineage_origin_id is not None:
                latest_version = 0
                rows = self.conn.execute(
                    "SELECT asset_id, version, data FROM assets WHERE project_id = ? AND asset_type = ?",
                    (asset.project_id, asset.asset_type.value),
                ).fetchall()
                for existing_row in rows:
                    existing_asset = Asset.model_validate_json(existing_row["data"])
                    if existing_asset.branch == asset.branch and (existing_asset.asset_id == lineage_origin_id or existing_asset.structured_data.get("origin_asset_id") == lineage_origin_id):
                        latest_version = max(latest_version, int(existing_row["version"]))
            if expected_version is not None and latest_version != expected_version:
                raise ValueError("A newer asset version already exists")
            if asset.version <= 1:
                asset.version = current_version + 1
            self.conn.execute(
                """
                INSERT OR REPLACE INTO assets (asset_id, project_id, asset_type, version, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.asset_id,
                    asset.project_id,
                    asset.asset_type.value,
                    asset.version,
                    asset.created_at.isoformat(),
                    asset.updated_at.isoformat(),
                    asset.model_dump_json(),
                ),
            )
            self.conn.commit()
        return asset

    def list_assets(self, project_id: str, asset_type: AssetType | None = None, *, branch: str | None = None) -> list[Asset]:
        with self._lock:
            if asset_type is None:
                rows = self.conn.execute(
                    "SELECT data FROM assets WHERE project_id = ? ORDER BY created_at ASC, version ASC",
                    (project_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT data FROM assets WHERE project_id = ? AND asset_type = ? ORDER BY version ASC",
                    (project_id, asset_type.value),
                ).fetchall()
        assets = [Asset.model_validate_json(row["data"]) for row in rows]
        if branch is not None:
            assets = [asset for asset in assets if asset.branch == branch]
        return assets

    def get_latest_asset(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> Asset | None:
        assets = self.list_assets(project_id, asset_type, branch=branch)
        if not assets:
            return None
        return assets[-1]

    def get_asset_by_id(self, asset_id: str) -> Asset | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM assets WHERE asset_id = ? ORDER BY version DESC LIMIT 1", (asset_id,)).fetchone()
        if row is None:
            return None
        return Asset.model_validate_json(row["data"])

    def list_asset_versions(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> list[Asset]:
        return self.list_assets(project_id, asset_type, branch=branch)

    def create_task(self, task: TaskRecord) -> TaskRecord:
        return self.save_task(task)

    def save_task(self, task: TaskRecord) -> TaskRecord:
        updated_at = task.finished_at or task.started_at or task.created_at
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO tasks (task_id, project_id, parent_task_id, status, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    task.parent_task_id,
                    task.status.value,
                    task.created_at.isoformat(),
                    updated_at.isoformat(),
                    task.model_dump_json(),
                ),
            )
            self.conn.commit()
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return TaskRecord.model_validate_json(row["data"])

    def list_tasks(self, project_id: str | None = None, *, branch: str | None = None) -> list[TaskRecord]:
        with self._lock:
            if project_id is None:
                rows = self.conn.execute("SELECT data FROM tasks ORDER BY created_at ASC").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT data FROM tasks WHERE project_id = ? ORDER BY created_at ASC",
                    (project_id,),
                ).fetchall()
        tasks = [TaskRecord.model_validate_json(row["data"]) for row in rows]
        if branch is not None:
            tasks = [task for task in tasks if task.branch == branch]
        return tasks

    def append_event(self, event: EventRecord) -> EventRecord:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO events (event_id, project_id, task_id, event_type, timestamp, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.project_id,
                    event.task_id,
                    event.event_type.value,
                    event.timestamp.isoformat(),
                    event.model_dump_json(),
                ),
            )
            self.conn.commit()
        return event

    def list_events(self, task_id: str) -> list[EventRecord]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM events WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,),
            ).fetchall()
        return [EventRecord.model_validate_json(row["data"]) for row in rows]

    def save_project_claim(self, claim: ProjectExecutionClaim) -> ProjectExecutionClaim:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO project_claims (project_id, lease_expires_at, data)
                VALUES (?, ?, ?)
                """,
                (claim.project_id, claim.lease_expires_at.isoformat(), claim.model_dump_json()),
            )
            self.conn.commit()
        return claim

    def get_project_claim(self, project_id: str) -> ProjectExecutionClaim | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM project_claims WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        return ProjectExecutionClaim.model_validate_json(row["data"])

    def list_project_claims(self) -> list[ProjectExecutionClaim]:
        with self._lock:
            rows = self.conn.execute("SELECT data FROM project_claims ORDER BY lease_expires_at ASC").fetchall()
        return [ProjectExecutionClaim.model_validate_json(row["data"]) for row in rows]

    def append_runtime_audit(self, record: RuntimeAuditRecord) -> RuntimeAuditRecord:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO runtime_audits (audit_id, project_id, created_at, data)
                VALUES (?, ?, ?, ?)
                """,
                (record.audit_id, record.project_id, record.created_at.isoformat(), record.model_dump_json()),
            )
            self.conn.commit()
        return record

    def list_runtime_audits(self, project_id: str | None = None) -> list[RuntimeAuditRecord]:
        with self._lock:
            if project_id is None:
                rows = self.conn.execute(
                    "SELECT data FROM runtime_audits ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT data FROM runtime_audits WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
        return [RuntimeAuditRecord.model_validate_json(row["data"]) for row in rows]

    def delete_project_claim(self, project_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM project_claims WHERE project_id = ?", (project_id,))
            self.conn.commit()

    def delete_asset(self, asset_id: str) -> Asset:
        with self._lock:
            row = self.conn.execute("SELECT data FROM assets WHERE asset_id = ? ORDER BY version DESC LIMIT 1", (asset_id,)).fetchone()
            if row is None:
                raise ValueError(f"Asset {asset_id} not found")
            asset = Asset.model_validate_json(row["data"])
            asset.is_deleted = True
            asset.deleted_at = datetime.now(timezone.utc)
            self.conn.execute(
                """
                INSERT OR REPLACE INTO assets (asset_id, project_id, asset_type, version, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (asset.asset_id, asset.project_id, asset.asset_type.value, asset.version, asset.created_at.isoformat(), asset.updated_at.isoformat(), asset.model_dump_json()),
            )
            self.conn.commit()
        return asset

    def list_trash(self, project_id: str) -> list[Asset]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM assets WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [Asset.model_validate_json(row["data"]) for row in rows if Asset.model_validate_json(row["data"]).is_deleted]

    def restore_asset(self, asset_id: str) -> Asset:
        with self._lock:
            row = self.conn.execute("SELECT data FROM assets WHERE asset_id = ? ORDER BY version DESC LIMIT 1", (asset_id,)).fetchone()
            if row is None:
                raise ValueError(f"Asset {asset_id} not found")
            asset = Asset.model_validate_json(row["data"])
            asset.is_deleted = False
            asset.deleted_at = None
            self.conn.execute(
                """
                INSERT OR REPLACE INTO assets (asset_id, project_id, asset_type, version, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (asset.asset_id, asset.project_id, asset.asset_type.value, asset.version, asset.created_at.isoformat(), asset.updated_at.isoformat(), asset.model_dump_json()),
            )
            self.conn.commit()
        return asset

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def save_rule(self, rule: Rule) -> Rule:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO rules (rule_id, layer, genre, project_id, name, description, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rule.rule_id, rule.layer.value, rule.genre, rule.project_id, rule.name, rule.description, int(rule.enabled)),
            )
            self.conn.commit()
        return rule

    def _row_to_rule(self, row: sqlite3.Row) -> Rule:
        return Rule(
            rule_id=row["rule_id"],
            layer=RuleLayer(row["layer"]),
            genre=row["genre"],
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            enabled=bool(row["enabled"]),
        )

    def get_rule(self, rule_id: str) -> Rule | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM rules WHERE rule_id = ?", (rule_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_rule(row)

    def get_rules(self, *, layer: str | None = None, genre: str | None = None, project_id: str | None = None, include_disabled: bool = False) -> list[Rule]:
        query = "SELECT * FROM rules WHERE 1 = 1"
        params: list = []
        if not include_disabled:
            query += " AND enabled = 1"
        if layer:
            query += " AND layer = ?"
            params.append(layer)
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def delete_rule(self, rule_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            self.conn.commit()

    def seed_universal_rules(self) -> list[Rule]:
        from storyforge.execution.rules import seed_universal_rules as _seed
        existing_ids = {r["rule_id"] for r in self.conn.execute("SELECT rule_id FROM rules WHERE layer = 'universal'").fetchall()}
        new_rules = [r for r in _seed() if r.rule_id not in existing_ids]
        for rule in new_rules:
            self.save_rule(rule)
        return self.get_rules(layer="universal")
