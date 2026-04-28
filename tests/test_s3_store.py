from __future__ import annotations

from pathlib import Path

import pytest

from storyforge.config import load_storage_config
from storyforge.domain.models import Asset, AssetType, Project
from storyforge.execution.rules import Rule, RuleLayer
from storyforge.persistence import S3StoryForgeStore, SQLiteStoryForgeStore, create_storyforge_store


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes | str, ContentType: str) -> None:
        payload = Body if isinstance(Body, bytes) else Body.encode("utf-8")
        self.objects[(Bucket, Key)] = payload

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _FakeBody]:
        return {"Body": _FakeBody(self.objects[(Bucket, Key)])}


def test_load_storage_config_reads_s3_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    default_db_path = tmp_path / "storyforge.db"
    monkeypatch.setenv("STORYFORGE_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_BUCKET", "storyforge-assets")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_PREFIX", "stories/")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_REGION", "us-east-1")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_ENDPOINT_URL", "https://s3.example.com")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_SECRET_ACCESS_KEY", "secret-key")

    config = load_storage_config(str(default_db_path))

    assert config.backend == "s3"
    assert config.db_path == str(default_db_path)
    assert config.s3_bucket == "storyforge-assets"
    assert config.s3_prefix == "stories/"
    assert config.s3_region == "us-east-1"
    assert config.s3_endpoint_url == "https://s3.example.com"
    assert config.s3_access_key_id == "key-id"
    assert config.s3_secret_access_key == "secret-key"


def test_create_storyforge_store_builds_s3_store_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import storyforge.persistence as persistence

    captured: dict[str, str] = {}

    class FakeConfiguredS3Store:
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
            object_client: object | None = None,
        ) -> None:
            captured.update(
                {
                    "db_path": db_path,
                    "bucket": bucket,
                    "prefix": prefix,
                    "region": region,
                    "endpoint_url": endpoint_url,
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                }
            )

    monkeypatch.setenv("STORYFORGE_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_BUCKET", "storyforge-assets")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_PREFIX", "archive/")
    monkeypatch.setenv("STORYFORGE_STORAGE_S3_REGION", "us-west-2")
    monkeypatch.setattr(persistence, "S3StoryForgeStore", FakeConfiguredS3Store)

    store = persistence.create_storyforge_store(str(tmp_path / "storyforge.db"))

    assert isinstance(store, FakeConfiguredS3Store)
    assert captured == {
        "db_path": str(tmp_path / "storyforge.db"),
        "bucket": "storyforge-assets",
        "prefix": "archive/",
        "region": "us-west-2",
        "endpoint_url": "",
        "access_key_id": "",
        "secret_access_key": "",
    }


def test_s3_storyforge_store_persists_asset_content_in_object_storage(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.db"
    object_client = FakeS3Client()
    store = S3StoryForgeStore(
        str(db_path),
        "storyforge-assets",
        prefix="stories/",
        object_client=object_client,
    )

    project = store.create_project(Project(idea="A hidden archive is mirrored to cloud storage."))
    saved_asset = store.save_asset(
        Asset(
            project_id=project.project_id,
            asset_type=AssetType.chapter,
            content="Chapter body from object storage.",
            structured_data={"chapter_number": 1},
        )
    )

    metadata_store = SQLiteStoryForgeStore(str(db_path))
    metadata_asset = metadata_store.get_asset_by_id(saved_asset.asset_id)
    hydrated_asset = store.get_asset_by_id(saved_asset.asset_id)
    chapter_assets = store.list_assets(project.project_id, AssetType.chapter)

    assert metadata_asset is not None
    # Hybrid design: SQLite stores the serialized Asset; S3 is the content source of truth.
    assert hydrated_asset is not None
    assert hydrated_asset.content == "Chapter body from object storage."
    assert [asset.content for asset in chapter_assets] == ["Chapter body from object storage."]
    assert list(object_client.objects.values()) == [b"Chapter body from object storage."]

    metadata_store.close()
    store.close()


def test_s3_storyforge_store_delegates_rules_to_metadata_store(tmp_path: Path) -> None:
    store = S3StoryForgeStore(
        str(tmp_path / "metadata.db"),
        "storyforge-assets",
        object_client=FakeS3Client(),
    )

    rule = store.save_rule(Rule(layer=RuleLayer.custom, project_id="proj_x", name="rule", description="desc"))
    disabled_rule = store.save_rule(
        Rule(layer=RuleLayer.custom, project_id="proj_x", name="disabled", description="desc", enabled=False)
    )

    assert store.get_rule(rule.rule_id) == rule
    assert store.get_rules(project_id="proj_x") == [rule]
    assert store.get_rules(project_id="proj_x", include_disabled=True) == [rule, disabled_rule]

    store.close()
