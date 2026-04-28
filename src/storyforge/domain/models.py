from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectPhase(str, Enum):
    intake = "intake"
    briefing = "briefing"
    outlining = "outlining"
    drafting = "drafting"
    review = "review"
    publishing = "publishing"


class AssetType(str, Enum):
    brief = "brief"
    world = "world"
    characters = "characters"
    rules = "rules"
    timeline = "timeline"
    outline = "outline"
    chapter_summary = "chapter_summary"
    chapter = "chapter"
    review_note = "review_note"
    continuity_note = "continuity_note"


class TaskType(str, Enum):
    brief_generation = "brief_generation"
    asset_bootstrap = "asset_bootstrap"
    outline_generation = "outline_generation"
    chapter_generation = "chapter_generation"
    chapter_review = "chapter_review"
    export = "export"
    spot_fix = "spot_fix"


class TaskStatus(str, Enum):
    accepted = "accepted"
    queued = "queued"
    running = "running"
    waiting_retry = "waiting_retry"
    failed = "failed"
    cancelled = "cancelled"
    completed = "completed"


class EventType(str, Enum):
    accepted = "accepted"
    queued = "queued"
    started = "started"
    step_started = "step_started"
    step_progress = "step_progress"
    step_completed = "step_completed"
    waiting_retry = "waiting_retry"
    failed = "failed"
    cancelled = "cancelled"
    completed = "completed"
    asset_updated = "asset_updated"


class CollaboratorRecord(BaseModel):
    actor_id: str
    actor_name: str
    role: str = "editor"


class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: f"proj_{uuid4().hex[:12]}")
    idea: str
    title: str = ""
    brief: str = ""
    genre: str = ""
    tags: list[str] = Field(default_factory=list)
    audience: str = ""
    target_length: int = 0
    status: str = "active"
    current_phase: ProjectPhase = ProjectPhase.intake
    active_config_profile: str = "default"
    active_asset_version: int = 1
    latest_outline_version: int = 0
    latest_chapter_cursor: int = 0
    owner_id: str = ""
    collaborators: list[CollaboratorRecord] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=lambda: ["main"])
    auto_mode: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Asset(BaseModel):
    asset_id: str = Field(default_factory=lambda: f"asset_{uuid4().hex[:12]}")
    project_id: str
    asset_type: AssetType
    branch: str = "main"
    version: int = 1
    source: str = "human"
    content: str = ""
    structured_data: dict[str, Any] = Field(default_factory=dict)
    comments: list[dict[str, Any]] = Field(default_factory=list)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConfigSnapshot(BaseModel):
    provider: str = ""
    model: str = ""
    review_policy: str = "default"
    retry_limit: int = 0
    chapter_length_target: int = 0
    values: dict[str, Any] = Field(default_factory=dict)


class TaskRecord(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    project_id: str
    task_type: TaskType
    branch: str = "main"
    status: TaskStatus = TaskStatus.accepted
    priority: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    effective_config_snapshot: ConfigSnapshot = Field(default_factory=ConfigSnapshot)
    input_asset_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    parent_task_id: str | None = None
    retry_count: int = 0
    current_step: str = "accepted"
    progress: float = 0.0
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EventRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    project_id: str
    task_id: str
    event_type: EventType
    step: str
    message: str = ""
    progress: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class ProjectExecutionClaim(BaseModel):
    project_id: str
    worker_id: str
    claimed_at: datetime = Field(default_factory=utc_now)
    lease_expires_at: datetime
    lease_duration_seconds: float = 30.0
    heartbeat_interval_seconds: float = 10.0
    last_heartbeat_at: datetime | None = None


class RuntimeAuditRecord(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"audit_{uuid4().hex[:12]}")
    action: str
    project_id: str
    actor_worker_id: str
    claim_worker_id: str | None = None
    forced: bool = False
    stale: bool = False
    message: str = ""
    created_at: datetime = Field(default_factory=utc_now)
