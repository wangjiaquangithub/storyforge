from __future__ import annotations

import os

from storyforge.config import load_storage_config
from storyforge.persistence.sqlite_store import SQLiteStoryForgeStore
from storyforge.persistence.s3_store import S3StoryForgeStore

from storyforge.execution.store import StoryForgeStore


def create_storyforge_store(db_path: str) -> StoryForgeStore:
    config = load_storage_config(db_path)
    if config.backend == "s3" and config.s3_bucket:
        return S3StoryForgeStore(
            config.db_path,
            config.s3_bucket,
            prefix=config.s3_prefix,
            region=config.s3_region,
            endpoint_url=config.s3_endpoint_url,
            access_key_id=config.s3_access_key_id,
            secret_access_key=config.s3_secret_access_key,
        )
    return SQLiteStoryForgeStore(config.db_path)


__all__ = ["S3StoryForgeStore", "SQLiteStoryForgeStore", "create_storyforge_store"]
