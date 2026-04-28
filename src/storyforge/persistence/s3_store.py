from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None  # type: ignore
    ClientError = Exception  # type: ignore

from storyforge.domain.models import Asset, AssetType, EventRecord, ProjectExecutionClaim, RuntimeAuditRecord, TaskRecord
from storyforge.execution.rules import Rule
from storyforge.persistence.sqlite_store import SQLiteStoryForgeStore


class S3StoryForgeStore:
    def __init__(
        self,
        db_path: str,
        bucket: str,
        *,
        prefix: str = "",
        region: str = "",
        endpoint_url: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        object_client: Any | None = None,
    ) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._sqlite = SQLiteStoryForgeStore(db_path)
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        if object_client is not None:
            self._s3 = object_client
        elif boto3 is not None:
            kwargs: dict[str, str] = {}
            if region:
                kwargs["region_name"] = region
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            if access_key_id and secret_access_key:
                kwargs["aws_access_key_id"] = access_key_id
                kwargs["aws_secret_access_key"] = secret_access_key
            self._s3 = boto3.client("s3", **kwargs)
        else:
            raise RuntimeError("boto3 is required for S3 storage backend")

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------
    def create_project(self, project) -> Any:
        return self._sqlite.create_project(project)

    def save_project(self, project) -> Any:
        return self._sqlite.save_project(project)

    def get_project(self, project_id: str) -> Any | None:
        return self._sqlite.get_project(project_id)

    def list_projects(self) -> list:
        return self._sqlite.list_projects()

    # ------------------------------------------------------------------
    # Asset — content in S3, metadata in SQLite
    # ------------------------------------------------------------------
    def _asset_key(self, asset_id: str) -> str:
        parts = [self._prefix, f"{asset_id}.txt"] if self._prefix else [f"{asset_id}.txt"]
        return "/".join(parts)

    def save_asset(self, asset, *, expected_version: int | None = None, lineage_origin_id: str | None = None) -> Any:
        saved = self._sqlite.save_asset(asset, expected_version=expected_version, lineage_origin_id=lineage_origin_id)
        if saved.content:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self._asset_key(saved.asset_id),
                Body=saved.content,
                ContentType="text/plain; charset=utf-8",
            )
            saved.content = ""
        return saved

    def _hydrate_asset(self, asset) -> Asset:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=self._asset_key(asset.asset_id))
            asset.content = resp["Body"].read().decode("utf-8")
        except Exception:
            asset.content = ""
        return asset

    def list_assets(self, project_id: str, asset_type: AssetType | None = None, *, branch: str | None = None) -> list:
        assets = self._sqlite.list_assets(project_id, asset_type, branch=branch)
        return [self._hydrate_asset(a) for a in assets]

    def get_latest_asset(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> Any | None:
        asset = self._sqlite.get_latest_asset(project_id, asset_type, branch=branch)
        return self._hydrate_asset(asset) if asset else None

    def get_asset_by_id(self, asset_id: str) -> Any | None:
        asset = self._sqlite.get_asset_by_id(asset_id)
        return self._hydrate_asset(asset) if asset else None

    def list_asset_versions(self, project_id: str, asset_type: AssetType, *, branch: str | None = None) -> list:
        assets = self._sqlite.list_asset_versions(project_id, asset_type, branch=branch)
        return [self._hydrate_asset(a) for a in assets]

    # ------------------------------------------------------------------
    # Rule — delegated to SQLite
    # ------------------------------------------------------------------
    def save_rule(self, rule: Rule) -> Rule:
        return self._sqlite.save_rule(rule)

    def get_rule(self, rule_id: str) -> Rule | None:
        return self._sqlite.get_rule(rule_id)

    def get_rules(
        self,
        *,
        layer: str | None = None,
        genre: str | None = None,
        project_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[Rule]:
        return self._sqlite.get_rules(
            layer=layer,
            genre=genre,
            project_id=project_id,
            include_disabled=include_disabled,
        )

    def delete_rule(self, rule_id: str) -> None:
        self._sqlite.delete_rule(rule_id)

    def seed_universal_rules(self) -> list[Rule]:
        return self._sqlite.seed_universal_rules()

    # ------------------------------------------------------------------
    # Task / Event / Claim / Audit — delegated to SQLite
    # ------------------------------------------------------------------
    def create_task(self, task: TaskRecord) -> TaskRecord:
        return self._sqlite.create_task(task)

    def save_task(self, task: TaskRecord) -> TaskRecord:
        return self._sqlite.save_task(task)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._sqlite.get_task(task_id)

    def list_tasks(self, project_id: str | None = None, *, branch: str | None = None) -> list[TaskRecord]:
        return self._sqlite.list_tasks(project_id, branch=branch)

    def append_event(self, event: EventRecord) -> EventRecord:
        return self._sqlite.append_event(event)

    def list_events(self, task_id: str) -> list[EventRecord]:
        return self._sqlite.list_events(task_id)

    def save_project_claim(self, claim: ProjectExecutionClaim) -> ProjectExecutionClaim:
        return self._sqlite.save_project_claim(claim)

    def get_project_claim(self, project_id: str) -> ProjectExecutionClaim | None:
        return self._sqlite.get_project_claim(project_id)

    def list_project_claims(self) -> list[ProjectExecutionClaim]:
        return self._sqlite.list_project_claims()

    def append_runtime_audit(self, record: RuntimeAuditRecord) -> RuntimeAuditRecord:
        return self._sqlite.append_runtime_audit(record)

    def list_runtime_audits(self, project_id: str | None = None) -> list[RuntimeAuditRecord]:
        return self._sqlite.list_runtime_audits(project_id)

    def delete_project_claim(self, project_id: str) -> None:
        self._sqlite.delete_project_claim(project_id)

    def delete_asset(self, asset_id: str) -> Asset:
        return self._sqlite.delete_asset(asset_id)

    def list_trash(self, project_id: str) -> list[Asset]:
        return self._sqlite.list_trash(project_id)

    def restore_asset(self, asset_id: str) -> Asset:
        return self._sqlite.restore_asset(asset_id)

    def close(self) -> None:
        self._sqlite.close()
