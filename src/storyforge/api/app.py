from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
import json
import math
import queue
import re
import time

from fastapi import FastAPI, Header, HTTPException, Query, Body, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from storyforge.application.services import ProjectApplicationService, ProjectExecutionSummary, TaskSummary, _coerce_usage_count
from storyforge.domain.models import (
    Asset,
    AssetType,
    CollaboratorRecord,
    ConfigSnapshot,
    EventRecord,
    EventType,
    Project,
    ProjectExecutionClaim,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from storyforge.execution.quality import find_production_artifact_issues
from storyforge.execution.runtime import WorkerRuntime
from storyforge.execution.state_machine import TaskStateMachine
from storyforge.execution.store import StoryForgeStore
from storyforge.execution.rules import Rule, RuleLayer
from storyforge.execution.workflow import ClosedLoopService
from storyforge.persistence.sqlite_store import SQLiteStoryForgeStore
from storyforge.realtime import EventBus

DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[3] / ".storyforge" / "storyforge.db")
FRONTEND_INDEX_PATH = Path(__file__).resolve().parents[3] / "frontend" / "index.html"


class CreateProjectRequest(BaseModel):
    idea: str = Field(min_length=1)
    title: str = ""
    genre: str = ""
    tags: list[str] = Field(default_factory=list)
    audience: str = ""
    target_length: int = 0


class QueueTaskRequest(BaseModel):
    task_type: TaskType
    branch: str = "main"
    payload: dict = Field(default_factory=dict)
    config: ConfigSnapshot = Field(default_factory=ConfigSnapshot)


class TaskActionRequest(BaseModel):
    step: str
    progress: float = 0.0
    error: str | None = None


class FirstLoopRequest(BaseModel):
    chapter_number: int = 1
    auto_run: bool = False
    branch: str = "main"


class NextChapterRequest(BaseModel):
    from_chapter: int | None = None
    auto_run: bool = False
    branch: str = "main"


class ChapterLoopRequest(BaseModel):
    target_chapter: int | None = None
    auto_run: bool = False
    branch: str = "main"


class ChapterInfo(BaseModel):
    chapter_number: int
    title: str = ""
    status: str = "pending"
    asset_id: str | None = None
    review_approved: bool | None = None


class CreateAssetRequest(BaseModel):
    asset_type: AssetType
    content: str = ""
    source: str = "human"
    structured_data: dict = Field(default_factory=dict)


class UpdateAssetRequest(BaseModel):
    content: str = ""
    source: str = "human"
    branch: str = "main"
    base_version: int | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    role: Literal["author", "editor", "reviewer", "approver"] | None = None
    approval_status: Literal["draft", "reviewed", "approved", "rejected"] | None = None
    change_note: str | None = None
    structured_data: dict = Field(default_factory=dict)


class RollbackAssetRequest(BaseModel):
    target_version: int | None = None
    branch: str = "main"


class ExportProjectRequest(BaseModel):
    format: Literal["markdown", "text", "qidian", "jinjiang"] = "markdown"
    branch: str = "main"


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    genre: str | None = None
    tags: list[str] | None = None
    audience: str | None = None
    target_length: int | None = None
    idea: str | None = None


class GenerateTokenRequest(BaseModel):
    user_id: str


class RevokeTokenRequest(BaseModel):
    token: str


class AddCommentRequest(BaseModel):
    author: str = "anonymous"
    content: str
    branch: str = "main"
    anchor_type: Literal["asset", "paragraph", "text"] = "asset"
    paragraph_index: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    role: Literal["author", "editor", "reviewer", "approver"] | None = None
    status: Literal["open", "resolved"] = "open"

    @model_validator(mode="after")
    def validate_anchor(self) -> "AddCommentRequest":
        if self.paragraph_index is not None and self.paragraph_index < 0:
            raise ValueError("paragraph_index must be non-negative")
        if self.start_offset is not None and self.start_offset < 0:
            raise ValueError("start_offset must be non-negative")
        if self.end_offset is not None and self.end_offset < 0:
            raise ValueError("end_offset must be non-negative")
        if self.start_offset is not None and self.end_offset is not None and self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        if self.anchor_type == "paragraph" and self.paragraph_index is None:
            raise ValueError("paragraph_index is required for paragraph comments")
        if self.anchor_type == "text" and (self.start_offset is None or self.end_offset is None):
            raise ValueError("start_offset and end_offset are required for text comments")
        return self


class UpdateCommentRequest(BaseModel):
    branch: str = "main"
    content: str | None = None
    status: Literal["open", "resolved"] | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    role: Literal["author", "editor", "reviewer", "approver"] | None = None


class CreateBranchRequest(BaseModel):
    branch: str


class ForkBranchRequest(BaseModel):
    target_branch: str
    start_chapter: int | None = None
    end_chapter: int | None = None


class DrainResponse(BaseModel):
    processed_task_ids: list[str] = Field(default_factory=list)
    processed_count: int = 0


class SpotFixRequest(BaseModel):
    chapter_asset_id: str
    paragraph_indices: list[int]
    fix_instruction: str
    branch: str | None = None


class RuleCreateRequest(BaseModel):
    layer: str
    genre: str | None = None
    project_id: str | None = None
    name: str
    description: str
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None


class AutoModeToggle(BaseModel):
    auto_mode: bool


class ForeshadowingCreateRequest(BaseModel):
    branch: str = "main"
    title: str = Field(min_length=1)
    description: str = ""
    introduced_chapter: int = Field(gt=0)
    expected_resolution_chapter: int | None = Field(default=None, gt=0)


class ForeshadowingUpdateRequest(BaseModel):
    branch: str = "main"
    title: str | None = None
    description: str | None = None
    introduced_chapter: int | None = Field(default=None, gt=0)
    expected_resolution_chapter: int | None = Field(default=None, gt=0)
    resolved_chapter: int | None = Field(default=None, gt=0)
    status: Literal["planted", "developed", "resolved", "dropped"] | None = None


class ForeshadowingItem(BaseModel):
    foreshadowing_id: str
    project_id: str
    branch: str
    title: str
    description: str = ""
    introduced_chapter: int
    expected_resolution_chapter: int | None = None
    resolved_chapter: int | None = None
    status: str = "planted"


class ForeshadowingListResponse(BaseModel):
    items: list[ForeshadowingItem] = Field(default_factory=list)
    total: int = 0


class TimelineCreateRequest(BaseModel):
    branch: str = "main"
    chapter_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    description: str = ""
    story_time_label: str = ""
    order_index: int = 0
    characters: list[str] = Field(default_factory=list)
    location: str = ""


class TimelineUpdateRequest(BaseModel):
    branch: str = "main"
    chapter_number: int | None = Field(default=None, gt=0)
    title: str | None = None
    description: str | None = None
    story_time_label: str | None = None
    order_index: int | None = None
    characters: list[str] | None = None
    location: str | None = None


class TimelineItem(BaseModel):
    timeline_event_id: str
    project_id: str
    branch: str
    chapter_number: int
    title: str
    description: str = ""
    story_time_label: str = ""
    order_index: int = 0
    characters: list[str] = Field(default_factory=list)
    location: str = ""


class TimelineListResponse(BaseModel):
    items: list[TimelineItem] = Field(default_factory=list)
    total: int = 0


class PacingCurvePoint(BaseModel):
    chapter_number: int
    title: str = ""
    pacing_score: float | None = None
    conflict_intensity: float | None = None
    emotional_intensity: float | None = None
    status: Literal["known", "unknown"] = "unknown"
    review_asset_id: str | None = None


class PacingCurveResponse(BaseModel):
    project_id: str
    branch: str
    points: list[PacingCurvePoint] = Field(default_factory=list)


class QualityExperimentMetric(BaseModel):
    experiment: str
    variant: str
    review_count: int = 0
    approved_count: int = 0
    rewrite_count: int = 0
    issue_count: int = 0
    approval_rate: float = 0.0
    rewrite_rate: float = 0.0
    average_issue_count: float = 0.0
    failed_check_distribution: dict[str, int] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class QualityExperimentListResponse(BaseModel):
    project_id: str
    branch: str
    experiments: list[QualityExperimentMetric] = Field(default_factory=list)


class StyleProfileUpdateRequest(BaseModel):
    branch: str = "main"
    voice: str | None = None
    strengths: list[str] | None = None
    avoid: list[str] | None = None
    sensory_keywords: list[str] | None = None
    locked_fields: list[Literal["voice", "strengths", "avoid", "sensory_keywords"]] = Field(default_factory=list)


class RuntimeResponse(BaseModel):
    queued: bool
    project_id: str
    mode: str


class ProductionConsoleResponse(BaseModel):
    project_id: str
    branch: str
    auto_mode: bool
    task_summary: TaskSummary
    queue: list[TaskRecord] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)


class ClaimResponse(BaseModel):
    claim: ClaimStatusResponse | None = None


class ClaimActionResponse(BaseModel):
    project_id: str
    released: bool
    forced: bool = False


class ClaimStatusResponse(BaseModel):
    project_id: str
    worker_id: str
    claimed_at: str
    lease_expires_at: str
    lease_duration_seconds: float
    heartbeat_interval_seconds: float
    last_heartbeat_at: str | None = None
    heartbeat_age_seconds: float | None = None
    heartbeat_overdue: bool
    stale: bool
    seconds_until_expiry: float


class ClaimListResponse(BaseModel):
    claims: list[ClaimStatusResponse] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    offset: int = 0
    limit: int = 100
    stale_count: int = 0


class ClaimHealthSummaryResponse(BaseModel):
    total_claims: int = 0
    stale_claims: int = 0
    heartbeat_overdue_claims: int = 0
    healthy_claims: int = 0
    affected_projects: int = 0
    affected_workers: int = 0


class WorkerClaimHealthSummaryItem(BaseModel):
    worker_id: str
    total_claims: int = 0
    stale_claims: int = 0
    heartbeat_overdue_claims: int = 0
    healthy_claims: int = 0
    affected_projects: int = 0


class WorkerClaimHealthSummaryResponse(BaseModel):
    workers: list[WorkerClaimHealthSummaryItem] = Field(default_factory=list)
    total_workers: int = 0


class ProjectClaimHealthSummaryItem(BaseModel):
    project_id: str
    worker_id: str
    stale: bool
    heartbeat_overdue: bool
    seconds_until_expiry: float


class ProjectClaimHealthSummaryResponse(BaseModel):
    projects: list[ProjectClaimHealthSummaryItem] = Field(default_factory=list)
    total_projects: int = 0


class BulkClaimActionResponse(BaseModel):
    released_project_ids: list[str] = Field(default_factory=list)
    released_count: int = 0


class RuntimeAuditResponse(BaseModel):
    audit_id: str
    action: str
    project_id: str
    actor_worker_id: str
    claim_worker_id: str | None = None
    forced: bool
    stale: bool
    message: str
    created_at: str


class LeaseLossSummaryResponse(BaseModel):
    recent_count: int = 0
    affected_projects: int = 0
    latest_created_at: str | None = None
    latest_project_id: str | None = None


class WorkerAuditSummaryItem(BaseModel):
    worker_id: str
    total_count: int = 0
    lease_loss_count: int = 0
    forced_release_count: int = 0
    stale_release_count: int = 0


class WorkerAuditSummaryResponse(BaseModel):
    workers: list[WorkerAuditSummaryItem] = Field(default_factory=list)
    total_workers: int = 0


class ProjectAuditSummaryItem(BaseModel):
    project_id: str
    total_count: int = 0
    lease_loss_count: int = 0
    forced_release_count: int = 0
    stale_release_count: int = 0


class ProjectAuditSummaryResponse(BaseModel):
    projects: list[ProjectAuditSummaryItem] = Field(default_factory=list)
    total_projects: int = 0


class ActionAuditSummaryItem(BaseModel):
    action: str
    total_count: int = 0
    forced_count: int = 0
    stale_count: int = 0


class ActionAuditSummaryResponse(BaseModel):
    actions: list[ActionAuditSummaryItem] = Field(default_factory=list)
    total_actions: int = 0


class SeverityContributionItem(BaseModel):
    signal: str
    count: int = 0
    weight: int = 0
    contribution: int = 0


class WorkerSeveritySummaryItem(BaseModel):
    worker_id: str
    severity_score: int = 0
    severity_reasons: list[str] = Field(default_factory=list)
    severity_contributions: list[SeverityContributionItem] = Field(default_factory=list)
    stale_claims: int = 0
    heartbeat_overdue_claims: int = 0
    active_claims: int = 0
    recent_audit_count: int = 0
    lease_loss_count: int = 0
    forced_release_count: int = 0
    stale_release_count: int = 0


class WorkerSeveritySummaryResponse(BaseModel):
    workers: list[WorkerSeveritySummaryItem] = Field(default_factory=list)
    total_workers: int = 0


class ProjectSeveritySummaryItem(BaseModel):
    project_id: str
    worker_id: str | None = None
    severity_score: int = 0
    severity_reasons: list[str] = Field(default_factory=list)
    severity_contributions: list[SeverityContributionItem] = Field(default_factory=list)
    stale_claim: bool = False
    heartbeat_overdue: bool = False
    recent_audit_count: int = 0
    lease_loss_count: int = 0
    forced_release_count: int = 0
    stale_release_count: int = 0


class ProjectSeveritySummaryResponse(BaseModel):
    projects: list[ProjectSeveritySummaryItem] = Field(default_factory=list)
    total_projects: int = 0


class JoinedSeveritySummaryItem(BaseModel):
    scope: str
    project_id: str | None = None
    worker_id: str | None = None
    severity_score: int = 0
    severity_reasons: list[str] = Field(default_factory=list)
    severity_contributions: list[SeverityContributionItem] = Field(default_factory=list)
    stale_claims: int = 0
    heartbeat_overdue_claims: int = 0
    active_claims: int = 0
    recent_audit_count: int = 0
    lease_loss_count: int = 0
    forced_release_count: int = 0
    stale_release_count: int = 0


class JoinedSeveritySummaryResponse(BaseModel):
    items: list[JoinedSeveritySummaryItem] = Field(default_factory=list)
    total_items: int = 0


class SeverityFocusSummaryResponse(BaseModel):
    claim_health_dominant: int = 0
    recovery_noise_dominant: int = 0
    mixed_or_neutral: int = 0
    total_items: int = 0


class SeverityWeightsResponse(BaseModel):
    stale_claim_weight: int = 100
    heartbeat_overdue_weight: int = 50
    lease_loss_weight: int = 20
    forced_release_weight: int = 10
    stale_release_weight: int = 5
    recent_audit_weight: int = 1


class SeverityPresetResponse(BaseModel):
    name: str
    window: str | None = None
    severity_focus: str | None = None
    worker_severity_limit: int | None = None
    project_severity_limit: int | None = None
    worker_summary_limit: int | None = None
    project_summary_limit: int | None = None
    action_summary_limit: int | None = None


class SeverityPresetDiscoveryResponse(BaseModel):
    name: str
    category: str
    labels: list[str] = Field(default_factory=list)
    window: str | None = None
    severity_focus: str | None = None
    worker_severity_limit: int | None = None
    project_severity_limit: int | None = None
    worker_summary_limit: int | None = None
    project_summary_limit: int | None = None
    action_summary_limit: int | None = None
    weights: SeverityWeightsResponse = Field(default_factory=SeverityWeightsResponse)


class SeverityPresetDefinition(BaseModel):
    name: str
    category: str
    labels: list[str] = Field(default_factory=list)
    window: str | None = None
    severity_focus: str | None = None
    worker_severity_limit: int | None = None
    project_severity_limit: int | None = None
    worker_summary_limit: int | None = None
    project_summary_limit: int | None = None
    action_summary_limit: int | None = None
    weights: SeverityWeightsResponse = Field(default_factory=SeverityWeightsResponse)


SEVERITY_PRESETS: dict[str, SeverityPresetDefinition] = {
    "balanced": SeverityPresetDefinition(
        name="balanced",
        category="general",
        labels=["default", "balanced", "overview"],
        window="last_day",
        severity_focus=None,
        worker_severity_limit=10,
        project_severity_limit=10,
        worker_summary_limit=10,
        project_summary_limit=10,
        action_summary_limit=10,
        weights=SeverityWeightsResponse(),
    ),
    "claim_health": SeverityPresetDefinition(
        name="claim_health",
        category="ownership",
        labels=["ownership", "claim-health", "triage"],
        window="last_week",
        severity_focus="claim_health",
        worker_severity_limit=10,
        project_severity_limit=10,
        worker_summary_limit=10,
        project_summary_limit=10,
        action_summary_limit=10,
        weights=SeverityWeightsResponse(
            stale_claim_weight=150,
            heartbeat_overdue_weight=100,
            lease_loss_weight=10,
            forced_release_weight=5,
            stale_release_weight=5,
            recent_audit_weight=0,
        ),
    ),
    "recovery_noise": SeverityPresetDefinition(
        name="recovery_noise",
        category="recovery",
        labels=["recovery", "operator-churn", "triage"],
        window="last_hour",
        severity_focus="recovery_noise",
        worker_severity_limit=3,
        project_severity_limit=3,
        worker_summary_limit=3,
        project_summary_limit=3,
        action_summary_limit=3,
        weights=SeverityWeightsResponse(
            stale_claim_weight=25,
            heartbeat_overdue_weight=25,
            lease_loss_weight=40,
            forced_release_weight=20,
            stale_release_weight=15,
            recent_audit_weight=3,
        ),
    ),
}

CLAIM_HEALTH_CONTRIBUTION_SIGNALS = {"stale_claim", "heartbeat_overdue"}
RECOVERY_NOISE_CONTRIBUTION_SIGNALS = {"lease_loss", "forced_release", "stale_release", "recent_audit"}


class SeverityPresetListResponse(BaseModel):
    presets: list[SeverityPresetDiscoveryResponse] = Field(default_factory=list)
    total: int = 0


class RuntimeAuditListResponse(BaseModel):
    records: list[RuntimeAuditResponse] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    offset: int = 0
    limit: int = 100
    severity_preset: SeverityPresetResponse | None = None
    lease_loss_summary: LeaseLossSummaryResponse = Field(default_factory=LeaseLossSummaryResponse)
    claim_health_summary: ClaimHealthSummaryResponse = Field(default_factory=ClaimHealthSummaryResponse)
    worker_claim_health_summary: WorkerClaimHealthSummaryResponse = Field(default_factory=WorkerClaimHealthSummaryResponse)
    project_claim_health_summary: ProjectClaimHealthSummaryResponse = Field(default_factory=ProjectClaimHealthSummaryResponse)
    severity_weights: SeverityWeightsResponse = Field(default_factory=SeverityWeightsResponse)
    worker_severity_focus_summary: SeverityFocusSummaryResponse = Field(default_factory=SeverityFocusSummaryResponse)
    project_severity_focus_summary: SeverityFocusSummaryResponse = Field(default_factory=SeverityFocusSummaryResponse)
    joined_severity_focus_summary: SeverityFocusSummaryResponse = Field(default_factory=SeverityFocusSummaryResponse)
    worker_severity_summary: WorkerSeveritySummaryResponse = Field(default_factory=WorkerSeveritySummaryResponse)
    project_severity_summary: ProjectSeveritySummaryResponse = Field(default_factory=ProjectSeveritySummaryResponse)
    joined_severity_summary: JoinedSeveritySummaryResponse = Field(default_factory=JoinedSeveritySummaryResponse)
    worker_summary: WorkerAuditSummaryResponse = Field(default_factory=WorkerAuditSummaryResponse)
    project_summary: ProjectAuditSummaryResponse = Field(default_factory=ProjectAuditSummaryResponse)
    action_summary: ActionAuditSummaryResponse = Field(default_factory=ActionAuditSummaryResponse)


class AppState:
    def __init__(self, store: StoryForgeStore) -> None:
        self.store = store
        self.event_bus = EventBus()
        self.state_machine = TaskStateMachine()
        self.workflow = ClosedLoopService(self.store, self.state_machine, self.event_bus)
        self.application = ProjectApplicationService(self.workflow)
        self.runtime = WorkerRuntime(self.workflow)


class RateLimiter:
    """Simple sliding-window rate limiter per client key."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Prune old entries
        self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]
        remaining = self.max_requests - len(self._requests[key])
        if remaining <= 0:
            return False, 0
        self._requests[key].append(now)
        return True, remaining - 1

    def cleanup(self) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        stale_keys = [k for k, v in self._requests.items() if not v or all(ts <= cutoff for ts in v)]
        for k in stale_keys:
            del self._requests[k]


class TokenAuth:
    """Simple API token authentication."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}  # token -> user_id

    def register_token(self, token: str, user_id: str) -> None:
        self._tokens[token] = user_id

    def verify(self, token: str) -> str | None:
        return self._tokens.get(token)

    def generate_token(self, user_id: str) -> str:
        import secrets
        token = f"sf_{secrets.token_urlsafe(24)}"
        self._tokens[token] = user_id
        return token

    def list_tokens(self, user_id: str) -> list[str]:
        return [t for t, uid in self._tokens.items() if uid == user_id]

    def revoke_token(self, token: str) -> bool:
        return self._tokens.pop(token, None) is not None



def _to_claim_status_response(claim: ProjectExecutionClaim, *, now: datetime) -> ClaimStatusResponse:
    seconds_until_expiry = (claim.lease_expires_at - now).total_seconds()
    stale = seconds_until_expiry <= 0
    heartbeat_age_seconds: float | None = None
    if claim.last_heartbeat_at is not None:
        heartbeat_age_seconds = max(0.0, (now - claim.last_heartbeat_at).total_seconds())
    heartbeat_overdue = (
        claim.last_heartbeat_at is not None and heartbeat_age_seconds is not None and heartbeat_age_seconds > claim.heartbeat_interval_seconds * 2
    )
    return ClaimStatusResponse(
        project_id=claim.project_id,
        worker_id=claim.worker_id,
        claimed_at=claim.claimed_at.isoformat(),
        lease_expires_at=claim.lease_expires_at.isoformat(),
        lease_duration_seconds=claim.lease_duration_seconds,
        heartbeat_interval_seconds=claim.heartbeat_interval_seconds,
        last_heartbeat_at=claim.last_heartbeat_at.isoformat() if claim.last_heartbeat_at is not None else None,
        heartbeat_age_seconds=heartbeat_age_seconds,
        heartbeat_overdue=heartbeat_overdue,
        stale=stale,
        seconds_until_expiry=seconds_until_expiry,
    )



def _parse_iso8601_datetime(value: str | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    normalized_value = value
    if "T" in normalized_value and normalized_value.count(" ") == 1:
        datetime_part, timezone_part = normalized_value.split(" ", 1)
        if len(timezone_part) == 5 and timezone_part[2] == ":" and timezone_part.replace(":", "").isdigit():
            normalized_value = f"{datetime_part}+{timezone_part}"
    try:
        return datetime.fromisoformat(normalized_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid ISO 8601 datetime") from exc



def _resolve_relative_audit_window(window: str | None, *, now: datetime) -> tuple[datetime | None, datetime | None]:
    if window is None:
        return None, None
    window_key = window.strip().lower()
    window_durations = {
        "last_hour": timedelta(hours=1),
        "last_day": timedelta(days=1),
        "last_week": timedelta(days=7),
    }
    duration = window_durations.get(window_key)
    if duration is None:
        raise HTTPException(status_code=400, detail="window must be one of: last_hour, last_day, last_week")
    return now - duration, now



def _resolve_severity_preset(preset: str | None) -> SeverityPresetDefinition | None:
    if preset is None:
        return None
    preset_key = preset.strip().lower()
    resolved_preset = SEVERITY_PRESETS.get(preset_key)
    if resolved_preset is None:
        raise HTTPException(
            status_code=400,
            detail="severity_preset must be one of: balanced, claim_health, recovery_noise",
        )
    return resolved_preset



def _to_severity_preset_response(preset: SeverityPresetDefinition | None) -> SeverityPresetResponse | None:
    if preset is None:
        return None
    return SeverityPresetResponse(
        name=preset.name,
        window=preset.window,
        severity_focus=preset.severity_focus,
        worker_severity_limit=preset.worker_severity_limit,
        project_severity_limit=preset.project_severity_limit,
        worker_summary_limit=preset.worker_summary_limit,
        project_summary_limit=preset.project_summary_limit,
        action_summary_limit=preset.action_summary_limit,
    )



def _to_severity_preset_discovery_response(preset: SeverityPresetDefinition) -> SeverityPresetDiscoveryResponse:
    return SeverityPresetDiscoveryResponse(
        name=preset.name,
        category=preset.category,
        labels=preset.labels,
        window=preset.window,
        severity_focus=preset.severity_focus,
        worker_severity_limit=preset.worker_severity_limit,
        project_severity_limit=preset.project_severity_limit,
        worker_summary_limit=preset.worker_summary_limit,
        project_summary_limit=preset.project_summary_limit,
        action_summary_limit=preset.action_summary_limit,
        weights=preset.weights,
    )



def _resolve_contribution_filter(value: str | None) -> str | None:
    if value is None:
        return None
    filter_key = value.strip().lower()
    if filter_key not in {"claim_health", "recovery_noise"}:
        raise HTTPException(status_code=400, detail="severity_focus must be one of: claim_health, recovery_noise")
    return filter_key



def _contribution_focus_score(contributions: list[SeverityContributionItem], allowed_signals: set[str]) -> int:
    return sum(item.contribution for item in contributions if item.signal in allowed_signals)



def _contribution_focus_matches(contributions: list[SeverityContributionItem], focus: str | None) -> bool:
    if focus is None:
        return True
    if focus == "claim_health":
        primary_signals = CLAIM_HEALTH_CONTRIBUTION_SIGNALS
        secondary_signals = RECOVERY_NOISE_CONTRIBUTION_SIGNALS
    else:
        primary_signals = RECOVERY_NOISE_CONTRIBUTION_SIGNALS
        secondary_signals = CLAIM_HEALTH_CONTRIBUTION_SIGNALS
    primary_score = _contribution_focus_score(contributions, primary_signals)
    secondary_score = _contribution_focus_score(contributions, secondary_signals)
    return primary_score > 0 and primary_score > secondary_score



def _to_severity_focus_summary(contributions_by_item: list[list[SeverityContributionItem]]) -> SeverityFocusSummaryResponse:
    claim_health_dominant = 0
    recovery_noise_dominant = 0
    mixed_or_neutral = 0
    for contributions in contributions_by_item:
        claim_health_score = _contribution_focus_score(contributions, CLAIM_HEALTH_CONTRIBUTION_SIGNALS)
        recovery_noise_score = _contribution_focus_score(contributions, RECOVERY_NOISE_CONTRIBUTION_SIGNALS)
        if claim_health_score > recovery_noise_score and claim_health_score > 0:
            claim_health_dominant += 1
        elif recovery_noise_score > claim_health_score and recovery_noise_score > 0:
            recovery_noise_dominant += 1
        else:
            mixed_or_neutral += 1
    return SeverityFocusSummaryResponse(
        claim_health_dominant=claim_health_dominant,
        recovery_noise_dominant=recovery_noise_dominant,
        mixed_or_neutral=mixed_or_neutral,
        total_items=len(contributions_by_item),
    )



def _to_lease_loss_summary(records: list[RuntimeAuditResponse]) -> LeaseLossSummaryResponse:
    lease_loss_records = [record for record in records if record.action == "claim_heartbeat_lost"]
    if not lease_loss_records:
        return LeaseLossSummaryResponse()
    latest_record = max(lease_loss_records, key=lambda record: record.created_at)
    return LeaseLossSummaryResponse(
        recent_count=len(lease_loss_records),
        affected_projects=len({record.project_id for record in lease_loss_records}),
        latest_created_at=latest_record.created_at,
        latest_project_id=latest_record.project_id,
    )



def _to_claim_health_summary(claims: list[ClaimStatusResponse]) -> ClaimHealthSummaryResponse:
    stale_claims = sum(1 for claim in claims if claim.stale)
    heartbeat_overdue_claims = sum(1 for claim in claims if claim.heartbeat_overdue)
    healthy_claims = sum(1 for claim in claims if not claim.stale and not claim.heartbeat_overdue)
    return ClaimHealthSummaryResponse(
        total_claims=len(claims),
        stale_claims=stale_claims,
        heartbeat_overdue_claims=heartbeat_overdue_claims,
        healthy_claims=healthy_claims,
        affected_projects=len({claim.project_id for claim in claims}),
        affected_workers=len({claim.worker_id for claim in claims}),
    )



def _to_worker_claim_health_summary(
    claims: list[ClaimStatusResponse], *, limit: int | None = None
) -> WorkerClaimHealthSummaryResponse:
    summary_by_worker: dict[str, WorkerClaimHealthSummaryItem] = {}
    for claim in claims:
        summary = summary_by_worker.get(claim.worker_id)
        if summary is None:
            summary = WorkerClaimHealthSummaryItem(worker_id=claim.worker_id)
            summary_by_worker[claim.worker_id] = summary
        summary.total_claims += 1
        if claim.stale:
            summary.stale_claims += 1
        if claim.heartbeat_overdue:
            summary.heartbeat_overdue_claims += 1
        if not claim.stale and not claim.heartbeat_overdue:
            summary.healthy_claims += 1
    for summary in summary_by_worker.values():
        summary.affected_projects = len({claim.project_id for claim in claims if claim.worker_id == summary.worker_id})
    workers = sorted(
        summary_by_worker.values(),
        key=lambda item: (-item.stale_claims, -item.heartbeat_overdue_claims, -item.total_claims, item.worker_id),
    )
    if limit is not None:
        workers = workers[:limit]
    return WorkerClaimHealthSummaryResponse(workers=workers, total_workers=len(summary_by_worker))



def _to_project_claim_health_summary(
    claims: list[ClaimStatusResponse], *, limit: int | None = None
) -> ProjectClaimHealthSummaryResponse:
    projects = sorted(
        [
            ProjectClaimHealthSummaryItem(
                project_id=claim.project_id,
                worker_id=claim.worker_id,
                stale=claim.stale,
                heartbeat_overdue=claim.heartbeat_overdue,
                seconds_until_expiry=claim.seconds_until_expiry,
            )
            for claim in claims
        ],
        key=lambda item: (
            not item.stale,
            not item.heartbeat_overdue,
            item.seconds_until_expiry,
            item.project_id,
        ),
    )
    if limit is not None:
        projects = projects[:limit]
    return ProjectClaimHealthSummaryResponse(projects=projects, total_projects=len(claims))



def _build_severity_contributions(signal_specs: list[tuple[str, int, int]]) -> list[SeverityContributionItem]:
    contributions = [
        SeverityContributionItem(
            signal=signal,
            count=count,
            weight=weight,
            contribution=count * weight,
        )
        for signal, count, weight in signal_specs
        if count > 0 and weight > 0 and count * weight > 0
    ]
    contributions.sort(key=lambda item: (-item.contribution, -item.count, item.signal))
    return contributions



def _to_worker_severity_reasons(summary: WorkerSeveritySummaryItem) -> list[str]:
    reasons: list[tuple[int, str]] = []
    if summary.stale_claims:
        reasons.append((summary.stale_claims, f"{summary.stale_claims} stale claim{'s' if summary.stale_claims != 1 else ''}"))
    if summary.heartbeat_overdue_claims:
        reasons.append(
            (
                summary.heartbeat_overdue_claims,
                f"{summary.heartbeat_overdue_claims} heartbeat-overdue claim{'s' if summary.heartbeat_overdue_claims != 1 else ''}",
            )
        )
    if summary.lease_loss_count:
        reasons.append((summary.lease_loss_count, f"{summary.lease_loss_count} lease-loss audit{'s' if summary.lease_loss_count != 1 else ''}"))
    if summary.forced_release_count:
        reasons.append(
            (
                summary.forced_release_count,
                f"{summary.forced_release_count} forced release audit{'s' if summary.forced_release_count != 1 else ''}",
            )
        )
    if summary.stale_release_count:
        reasons.append(
            (
                summary.stale_release_count,
                f"{summary.stale_release_count} stale-release audit{'s' if summary.stale_release_count != 1 else ''}",
            )
        )
    if summary.recent_audit_count and not reasons:
        reasons.append((summary.recent_audit_count, f"{summary.recent_audit_count} recent audit{'s' if summary.recent_audit_count != 1 else ''}"))
    reasons.sort(key=lambda item: (-item[0], item[1]))
    return [reason for _, reason in reasons[:3]]



def _to_project_severity_reasons(summary: ProjectSeveritySummaryItem) -> list[str]:
    reasons: list[tuple[int, str]] = []
    if summary.stale_claim:
        reasons.append((1, "stale current claim"))
    if summary.heartbeat_overdue:
        reasons.append((1, "heartbeat-overdue current claim"))
    if summary.lease_loss_count:
        reasons.append((summary.lease_loss_count, f"{summary.lease_loss_count} lease-loss audit{'s' if summary.lease_loss_count != 1 else ''}"))
    if summary.forced_release_count:
        reasons.append(
            (
                summary.forced_release_count,
                f"{summary.forced_release_count} forced release audit{'s' if summary.forced_release_count != 1 else ''}",
            )
        )
    if summary.stale_release_count:
        reasons.append(
            (
                summary.stale_release_count,
                f"{summary.stale_release_count} stale-release audit{'s' if summary.stale_release_count != 1 else ''}",
            )
        )
    if summary.recent_audit_count and not reasons:
        reasons.append((summary.recent_audit_count, f"{summary.recent_audit_count} recent audit{'s' if summary.recent_audit_count != 1 else ''}"))
    reasons.sort(key=lambda item: (-item[0], item[1]))
    return [reason for _, reason in reasons[:3]]



def _to_worker_audit_summary(
    records: list[RuntimeAuditResponse], *, limit: int | None = None
) -> WorkerAuditSummaryResponse:
    summary_by_worker: dict[str, WorkerAuditSummaryItem] = {}
    for record in records:
        summary = summary_by_worker.get(record.actor_worker_id)
        if summary is None:
            summary = WorkerAuditSummaryItem(worker_id=record.actor_worker_id)
            summary_by_worker[record.actor_worker_id] = summary
        summary.total_count += 1
        if record.action == "claim_heartbeat_lost":
            summary.lease_loss_count += 1
        if record.action == "release_project_claim" and record.forced:
            summary.forced_release_count += 1
        if record.action == "release_stale_project_claim":
            summary.stale_release_count += 1
    workers = sorted(
        summary_by_worker.values(),
        key=lambda item: (-item.total_count, -item.lease_loss_count, item.worker_id),
    )
    if limit is not None:
        workers = workers[:limit]
    return WorkerAuditSummaryResponse(workers=workers, total_workers=len(summary_by_worker))



def _to_worker_severity_summary(
    claims: list[ClaimStatusResponse],
    records: list[RuntimeAuditResponse],
    *,
    weights: SeverityWeightsResponse,
    limit: int | None = None,
) -> WorkerSeveritySummaryResponse:
    summary_by_worker: dict[str, WorkerSeveritySummaryItem] = {}
    for claim in claims:
        summary = summary_by_worker.get(claim.worker_id)
        if summary is None:
            summary = WorkerSeveritySummaryItem(worker_id=claim.worker_id)
            summary_by_worker[claim.worker_id] = summary
        summary.active_claims += 1
        if claim.stale:
            summary.stale_claims += 1
        if claim.heartbeat_overdue:
            summary.heartbeat_overdue_claims += 1
    for record in records:
        summary = summary_by_worker.get(record.actor_worker_id)
        if summary is None:
            summary = WorkerSeveritySummaryItem(worker_id=record.actor_worker_id)
            summary_by_worker[record.actor_worker_id] = summary
        summary.recent_audit_count += 1
        if record.action == "claim_heartbeat_lost":
            summary.lease_loss_count += 1
        if record.action == "release_project_claim" and record.forced:
            summary.forced_release_count += 1
        if record.action == "release_stale_project_claim":
            summary.stale_release_count += 1
    for summary in summary_by_worker.values():
        summary.severity_score = (
            summary.stale_claims * weights.stale_claim_weight
            + summary.heartbeat_overdue_claims * weights.heartbeat_overdue_weight
            + summary.lease_loss_count * weights.lease_loss_weight
            + summary.forced_release_count * weights.forced_release_weight
            + summary.stale_release_count * weights.stale_release_weight
            + summary.recent_audit_count * weights.recent_audit_weight
        )
        summary.severity_contributions = _build_severity_contributions(
            [
                ("stale_claim", summary.stale_claims, weights.stale_claim_weight),
                ("heartbeat_overdue", summary.heartbeat_overdue_claims, weights.heartbeat_overdue_weight),
                ("lease_loss", summary.lease_loss_count, weights.lease_loss_weight),
                ("forced_release", summary.forced_release_count, weights.forced_release_weight),
                ("stale_release", summary.stale_release_count, weights.stale_release_weight),
                ("recent_audit", summary.recent_audit_count, weights.recent_audit_weight),
            ]
        )
        summary.severity_reasons = _to_worker_severity_reasons(summary)
    workers = sorted(
        summary_by_worker.values(),
        key=lambda item: (-item.severity_score, -item.stale_claims, -item.heartbeat_overdue_claims, item.worker_id),
    )
    if limit is not None:
        workers = workers[:limit]
    return WorkerSeveritySummaryResponse(workers=workers, total_workers=len(summary_by_worker))



def _to_project_audit_summary(
    records: list[RuntimeAuditResponse], *, limit: int | None = None
) -> ProjectAuditSummaryResponse:
    summary_by_project: dict[str, ProjectAuditSummaryItem] = {}
    for record in records:
        summary = summary_by_project.get(record.project_id)
        if summary is None:
            summary = ProjectAuditSummaryItem(project_id=record.project_id)
            summary_by_project[record.project_id] = summary
        summary.total_count += 1
        if record.action == "claim_heartbeat_lost":
            summary.lease_loss_count += 1
        if record.action == "release_project_claim" and record.forced:
            summary.forced_release_count += 1
        if record.action == "release_stale_project_claim":
            summary.stale_release_count += 1
    projects = sorted(
        summary_by_project.values(),
        key=lambda item: (-item.total_count, -item.lease_loss_count, item.project_id),
    )
    if limit is not None:
        projects = projects[:limit]
    return ProjectAuditSummaryResponse(projects=projects, total_projects=len(summary_by_project))



def _to_action_audit_summary(
    records: list[RuntimeAuditResponse], *, limit: int | None = None
) -> ActionAuditSummaryResponse:
    summary_by_action: dict[str, ActionAuditSummaryItem] = {}
    for record in records:
        summary = summary_by_action.get(record.action)
        if summary is None:
            summary = ActionAuditSummaryItem(action=record.action)
            summary_by_action[record.action] = summary
        summary.total_count += 1
        if record.forced:
            summary.forced_count += 1
        if record.stale:
            summary.stale_count += 1
    actions = sorted(
        summary_by_action.values(),
        key=lambda item: (-item.total_count, item.action),
    )
    if limit is not None:
        actions = actions[:limit]
    return ActionAuditSummaryResponse(actions=actions, total_actions=len(summary_by_action))



def _to_project_severity_summary(
    claims: list[ClaimStatusResponse],
    records: list[RuntimeAuditResponse],
    *,
    weights: SeverityWeightsResponse,
    limit: int | None = None,
) -> ProjectSeveritySummaryResponse:
    summary_by_project: dict[str, ProjectSeveritySummaryItem] = {}
    for claim in claims:
        summary = summary_by_project.get(claim.project_id)
        if summary is None:
            summary = ProjectSeveritySummaryItem(project_id=claim.project_id)
            summary_by_project[claim.project_id] = summary
        summary.worker_id = claim.worker_id
        summary.stale_claim = claim.stale
        summary.heartbeat_overdue = claim.heartbeat_overdue
    for record in records:
        summary = summary_by_project.get(record.project_id)
        if summary is None:
            summary = ProjectSeveritySummaryItem(project_id=record.project_id)
            summary_by_project[record.project_id] = summary
        summary.recent_audit_count += 1
        if record.action == "claim_heartbeat_lost":
            summary.lease_loss_count += 1
        if record.action == "release_project_claim" and record.forced:
            summary.forced_release_count += 1
        if record.action == "release_stale_project_claim":
            summary.stale_release_count += 1
    for summary in summary_by_project.values():
        summary.severity_score = (
            (weights.stale_claim_weight if summary.stale_claim else 0)
            + (weights.heartbeat_overdue_weight if summary.heartbeat_overdue else 0)
            + summary.lease_loss_count * weights.lease_loss_weight
            + summary.forced_release_count * weights.forced_release_weight
            + summary.stale_release_count * weights.stale_release_weight
            + summary.recent_audit_count * weights.recent_audit_weight
        )
        summary.severity_contributions = _build_severity_contributions(
            [
                ("stale_claim", 1 if summary.stale_claim else 0, weights.stale_claim_weight),
                ("heartbeat_overdue", 1 if summary.heartbeat_overdue else 0, weights.heartbeat_overdue_weight),
                ("lease_loss", summary.lease_loss_count, weights.lease_loss_weight),
                ("forced_release", summary.forced_release_count, weights.forced_release_weight),
                ("stale_release", summary.stale_release_count, weights.stale_release_weight),
                ("recent_audit", summary.recent_audit_count, weights.recent_audit_weight),
            ]
        )
        summary.severity_reasons = _to_project_severity_reasons(summary)
    projects = sorted(
        summary_by_project.values(),
        key=lambda item: (-item.severity_score, item.project_id),
    )
    if limit is not None:
        projects = projects[:limit]
    return ProjectSeveritySummaryResponse(projects=projects, total_projects=len(summary_by_project))



def _to_joined_severity_summary(
    worker_summary: WorkerSeveritySummaryResponse,
    project_summary: ProjectSeveritySummaryResponse,
    *,
    limit: int | None = None,
) -> JoinedSeveritySummaryResponse:
    items = [
        JoinedSeveritySummaryItem(
            scope="worker",
            worker_id=item.worker_id,
            severity_score=item.severity_score,
            severity_reasons=item.severity_reasons,
            severity_contributions=item.severity_contributions,
            stale_claims=item.stale_claims,
            heartbeat_overdue_claims=item.heartbeat_overdue_claims,
            active_claims=item.active_claims,
            recent_audit_count=item.recent_audit_count,
            lease_loss_count=item.lease_loss_count,
            forced_release_count=item.forced_release_count,
            stale_release_count=item.stale_release_count,
        )
        for item in worker_summary.workers
    ]
    items.extend(
        JoinedSeveritySummaryItem(
            scope="project",
            project_id=item.project_id,
            worker_id=item.worker_id,
            severity_score=item.severity_score,
            severity_reasons=item.severity_reasons,
            severity_contributions=item.severity_contributions,
            stale_claims=1 if item.stale_claim else 0,
            heartbeat_overdue_claims=1 if item.heartbeat_overdue else 0,
            active_claims=1 if item.worker_id is not None else 0,
            recent_audit_count=item.recent_audit_count,
            lease_loss_count=item.lease_loss_count,
            forced_release_count=item.forced_release_count,
            stale_release_count=item.stale_release_count,
        )
        for item in project_summary.projects
    )
    items = sorted(
        items,
        key=lambda item: (
            -item.severity_score,
            item.scope,
            item.project_id or "",
            item.worker_id or "",
        ),
    )
    total_items = len(items)
    if limit is not None:
        items = items[:limit]
    return JoinedSeveritySummaryResponse(items=items, total_items=total_items)



def _coerce_chapter_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.isdigit():
            chapter_number = int(stripped)
            return chapter_number if chapter_number > 0 else None
        return None
    return None



def _chapter_title(chapter: Asset, chapter_number: int) -> str:
    return str(chapter.structured_data.get("title") or f"第 {chapter_number} 章")



def _chapter_body_without_heading(chapter: Asset, chapter_number: int, title: str) -> str:
    content = chapter.content
    lines = content.splitlines(keepends=True)
    if not lines:
        return content
    first_line = lines[0].rstrip("\r\n")
    if not _is_duplicate_chapter_heading(first_line, chapter_number, title):
        return content
    return "".join(lines[1:]).lstrip("\r\n")



def _is_duplicate_chapter_heading(line: str, chapter_number: int, title: str) -> bool:
    markdown_match = re.fullmatch(r" {0,3}#{1,6}(?:[ \t]+|$)(.*?)(?:[ \t]+#+[ \t]*)?", line)
    normalized = markdown_match.group(1).strip() if markdown_match else line.strip()
    return normalized in {
        f"第 {chapter_number} 章：{title}",
        f"第{chapter_number}章 {title}",
        f"第{chapter_number}章：{title}",
        f"第 {chapter_number} 章 {title}",
        f"【章节 {chapter_number}】{title}",
    }



BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")



def _validate_branch_name(branch: str) -> str:
    if not BRANCH_NAME_PATTERN.fullmatch(branch):
        raise HTTPException(status_code=400, detail="Branch name must be 1-64 characters and contain only letters, numbers, dot, underscore, or hyphen")
    return branch



def _require_project_branch(project: Project, branch: str) -> str:
    branch = _validate_branch_name(branch)
    if branch not in (project.branches or ["main"]):
        raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found")
    return branch



def _asset_chapter_number(asset: Asset) -> int | None:
    return _coerce_chapter_number(asset.structured_data.get("chapter_number"))



def _sanitize_download_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    sanitized = sanitized.strip("._-")
    return sanitized or "download"



def _render_markdown_export(project: Project, chapters: list[Asset]) -> str:
    lines = [f"# {project.title or '未命名项目'}", ""]
    if project.idea:
        lines.extend([f"> {project.idea}", ""])
    for chapter in chapters:
        chapter_number = int(chapter.structured_data.get("chapter_number", 0))
        title = _chapter_title(chapter, chapter_number)
        lines.extend([
            f"## 第 {chapter_number} 章：{title}",
            "",
            _chapter_body_without_heading(chapter, chapter_number, title),
            "",
        ])
    return "\n".join(lines).strip() + "\n"



def _render_text_export(project: Project, chapters: list[Asset]) -> str:
    lines = [project.title or "未命名项目", ""]
    if project.idea:
        lines.extend([project.idea, ""])
    for chapter in chapters:
        chapter_number = int(chapter.structured_data.get("chapter_number", 0))
        title = _chapter_title(chapter, chapter_number)
        lines.extend([
            f"第 {chapter_number} 章：{title}",
            _chapter_body_without_heading(chapter, chapter_number, title),
            "",
        ])
    return "\n".join(lines).strip() + "\n"



def _render_qidian_export(project: Project, chapters: list[Asset]) -> str:
    lines = [f"《{project.title or '未命名项目'}》", ""]
    if project.idea:
        lines.extend([project.idea, ""])
    for chapter in chapters:
        chapter_number = int(chapter.structured_data.get("chapter_number", 0))
        title = _chapter_title(chapter, chapter_number)
        lines.extend([
            f"第{chapter_number}章 {title}",
            _chapter_body_without_heading(chapter, chapter_number, title),
            "",
        ])
    return "\n".join(lines).strip() + "\n"



def _render_jinjiang_export(project: Project, chapters: list[Asset]) -> str:
    lines = [f"【作品名】{project.title or '未命名项目'}", ""]
    if project.idea:
        lines.extend([f"【文案】{project.idea}", ""])
    for chapter in chapters:
        chapter_number = int(chapter.structured_data.get("chapter_number", 0))
        title = _chapter_title(chapter, chapter_number)
        lines.extend([
            f"【章节 {chapter_number}】{title}",
            _chapter_body_without_heading(chapter, chapter_number, title),
            "",
        ])
    return "\n".join(lines).strip() + "\n"



def _is_spot_fix_candidate(asset: Asset) -> bool:
    return asset.asset_type == AssetType.chapter and asset.structured_data.get("is_spot_fix") is True



def _is_foreshadowing_asset(asset: Asset) -> bool:
    return asset.asset_type == AssetType.continuity_note and asset.structured_data.get("kind") == "foreshadowing"



def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        coerced = float(value)
        return coerced if math.isfinite(coerced) else None
    return None



def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))



def _coerce_unit_float(value: object) -> float | None:
    coerced = _coerce_float(value)
    return _clamp_unit(coerced) if coerced is not None else None



def _pacing_metrics(values: object) -> tuple[float | None, float | None, float | None]:
    if not isinstance(values, dict):
        return None, None, None
    score = _coerce_unit_float(values.get("score"))
    conflict_intensity = _coerce_unit_float(values.get("conflict_intensity"))
    emotional_intensity = _coerce_unit_float(values.get("emotional_intensity"))
    dialogue_ratio = _coerce_float(values.get("dialogue_ratio"))
    sentence_count = _coerce_float(values.get("sentence_count"))
    scene_transitions = _coerce_float(values.get("scene_transitions"))
    if score is None and (dialogue_ratio is not None or sentence_count is not None or scene_transitions is not None):
        dialogue_component = 1.0 - min(abs((dialogue_ratio or 0.0) - 0.25) / 0.25, 1.0)
        sentence_component = min((sentence_count or 0.0) / 40.0, 1.0)
        transition_component = min((scene_transitions or 0.0) / 3.0, 1.0)
        score = round(_clamp_unit(dialogue_component * 0.5 + sentence_component * 0.2 + transition_component * 0.3), 3)
    if conflict_intensity is None and scene_transitions is not None:
        conflict_intensity = round(_clamp_unit(scene_transitions / 3.0), 3)
    if emotional_intensity is None and dialogue_ratio is not None:
        emotional_intensity = round(_clamp_unit(dialogue_ratio * 2.0), 3)
    return score, conflict_intensity, emotional_intensity



def _split_paragraphs(content: str) -> list[str]:
    return content.split("\n\n")



EXPORTERS: dict[str, tuple[str, str, Callable[[Project, list[Asset]], str]]] = {
    "markdown": ("text/markdown; charset=utf-8", "md", _render_markdown_export),
    "text": ("text/plain; charset=utf-8", "txt", _render_text_export),
    "qidian": ("text/plain; charset=utf-8", "txt", _render_qidian_export),
    "jinjiang": ("text/plain; charset=utf-8", "txt", _render_jinjiang_export),
}

BOOTSTRAP_ASSET_TYPES = (
    AssetType.world,
    AssetType.characters,
    AssetType.rules,
    AssetType.timeline,
    AssetType.style_profile,
    AssetType.foreshadowing,
)

CRITICAL_PATH_STAGES: tuple[tuple[str, TaskType, tuple[AssetType, ...]], ...] = (
    ("brief", TaskType.brief_generation, (AssetType.brief,)),
    ("asset_bootstrap", TaskType.asset_bootstrap, BOOTSTRAP_ASSET_TYPES),
    ("outline", TaskType.outline_generation, (AssetType.outline,)),
    ("chapter_draft", TaskType.chapter_generation, (AssetType.chapter,)),
    ("validation", TaskType.chapter_validation, (AssetType.validation_report,)),
    ("audit", TaskType.chapter_audit, (AssetType.audit_report,)),
    ("revise", TaskType.chapter_revision, (AssetType.chapter, AssetType.revision_delta)),
    ("re_audit", TaskType.chapter_reaudit, (AssetType.audit_report,)),
    ("final_save", TaskType.final_save, (AssetType.final_chapter,)),
    ("export_candidate", TaskType.export_candidate, (AssetType.export_candidate,)),
)

SYSTEM_GATE_ASSET_TYPES = {
    AssetType.validation_report,
    AssetType.audit_report,
    AssetType.revision_delta,
    AssetType.final_chapter,
    AssetType.export_candidate,
}



def create_app(*, store: StoryForgeStore | None = None, db_path: str | None = None, auth: TokenAuth | None = None) -> FastAPI:
    resolved_store = store or SQLiteStoryForgeStore(db_path or DEFAULT_DB_PATH)
    state = AppState(resolved_store)
    token_auth = auth or TokenAuth()

    def _project_or_404(project_id: str) -> Project:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def _require_branch_for_project(project_id: str, branch: str | None, *, default: str | None = None) -> str | None:
        project = _project_or_404(project_id)
        if branch is None:
            return default
        return _require_project_branch(project, branch)

    def _emit_asset_updated_event(project_id: str, asset_id: str, asset_type: str, action: str, *, branch: str | None = None) -> None:
        state.workflow.record_event(
            EventRecord(
                project_id=project_id,
                task_id="",
                event_type=EventType.asset_updated,
                step="asset",
                message=f"Asset {action}: {asset_id}",
                payload={"asset_id": asset_id, "asset_type": asset_type, "action": action, "branch": branch},
            )
        )

    def _asset_lineage_id(asset: Asset) -> str:
        return str(asset.structured_data.get("origin_asset_id") or asset.asset_id)

    def _asset_lineage_members(project_id: str, asset: Asset) -> list[Asset]:
        lineage_id = _asset_lineage_id(asset)
        return [
            candidate
            for candidate in state.store.list_assets(project_id, asset.asset_type, branch=asset.branch)
            if candidate.asset_id == lineage_id
            or candidate.structured_data.get("origin_asset_id") == lineage_id
            or (_is_spot_fix_candidate(candidate) and candidate.structured_data.get("original_asset_id") == lineage_id)
        ]

    def _save_asset_deletion_state(asset: Asset, *, is_deleted: bool) -> Asset:
        asset.is_deleted = is_deleted
        asset.deleted_at = utc_now() if is_deleted else None
        asset.updated_at = utc_now()
        return state.store.save_asset(asset, lineage_origin_id=_asset_lineage_id(asset))

    def _foreshadowing_asset_to_item(asset: Asset) -> ForeshadowingItem:
        data = asset.structured_data
        return ForeshadowingItem(
            foreshadowing_id=asset.asset_id,
            project_id=asset.project_id,
            branch=asset.branch,
            title=str(data.get("title") or ""),
            description=str(data.get("description") or asset.content or ""),
            introduced_chapter=int(data.get("introduced_chapter") or 0),
            expected_resolution_chapter=_coerce_chapter_number(data.get("expected_resolution_chapter")),
            resolved_chapter=_coerce_chapter_number(data.get("resolved_chapter")),
            status=str(data.get("status") or "planted"),
        )

    def _get_foreshadowing_asset(project_id: str, foreshadowing_id: str, branch: str) -> Asset:
        asset = state.store.get_asset_by_id(foreshadowing_id)
        if asset is None or asset.project_id != project_id or asset.branch != branch or asset.asset_type != AssetType.continuity_note or asset.structured_data.get("kind") != "foreshadowing" or asset.is_deleted:
            raise HTTPException(status_code=404, detail="Foreshadowing not found")
        return asset

    def _timeline_asset_to_item(asset: Asset) -> TimelineItem:
        data = asset.structured_data
        return TimelineItem(
            timeline_event_id=asset.asset_id,
            project_id=asset.project_id,
            branch=asset.branch,
            chapter_number=int(data.get("chapter_number") or 0),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or asset.content or ""),
            story_time_label=str(data.get("story_time_label") or ""),
            order_index=int(data.get("order_index") or 0),
            characters=[str(character) for character in data.get("characters", [])],
            location=str(data.get("location") or ""),
        )

    def _get_timeline_asset(project_id: str, timeline_event_id: str, branch: str) -> Asset:
        asset = state.store.get_asset_by_id(timeline_event_id)
        if asset is None or asset.project_id != project_id or asset.branch != branch or asset.asset_type != AssetType.timeline or asset.is_deleted:
            raise HTTPException(status_code=404, detail="Timeline event not found")
        return asset

    def _latest_timeline_assets(project_id: str, branch: str) -> list[Asset]:
        latest: dict[str, Asset] = {}
        for asset in state.store.list_assets(project_id, AssetType.timeline, branch=branch):
            existing = latest.get(asset.asset_id)
            if existing is None or (asset.version, asset.updated_at, asset.created_at) > (existing.version, existing.updated_at, existing.created_at):
                latest[asset.asset_id] = asset
        visible = [asset for asset in latest.values() if not asset.is_deleted]
        return sorted(visible, key=lambda asset: (_timeline_asset_to_item(asset).chapter_number, _timeline_asset_to_item(asset).order_index, asset.created_at))

    def _asset_summary(asset: Asset) -> dict:
        chapter_number = _coerce_chapter_number(asset.structured_data.get("chapter_number"))
        return {
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type.value,
            "branch": asset.branch,
            "version": asset.version,
            "source": asset.source,
            "chapter_number": chapter_number,
            "title": str(asset.structured_data.get("title") or ""),
            "kind": str(asset.structured_data.get("kind") or ""),
            "passed": asset.structured_data.get("passed"),
            "ready": asset.structured_data.get("ready"),
            "blocked_reason": str(asset.structured_data.get("blocked_reason") or ""),
            "created_at": asset.created_at.isoformat(),
            "updated_at": asset.updated_at.isoformat(),
        }

    def _task_summary(task: TaskRecord) -> dict:
        return {
            "task_id": task.task_id,
            "task_type": task.task_type.value,
            "status": task.status.value,
            "parent_task_id": task.parent_task_id,
            "chapter_number": _coerce_chapter_number(task.payload.get("chapter_number")),
            "progress": task.progress,
            "error": task.error,
            "input_asset_refs": task.input_asset_refs,
            "output_refs": task.output_refs,
        }

    def _task_assets(task: TaskRecord, refs: list[str]) -> list[Asset]:
        assets: list[Asset] = []
        for ref in refs:
            asset = state.store.get_asset_by_id(ref)
            if asset is not None and asset.project_id == task.project_id and asset.branch == task.branch and not asset.is_deleted:
                assets.append(asset)
        return assets

    def _critical_path_task(tasks: list[TaskRecord], task_type: TaskType, chapter_number: int) -> TaskRecord | None:
        matches = [
            task
            for task in tasks
            if task.task_type == task_type
            and _coerce_chapter_number(task.payload.get("chapter_number")) == chapter_number
            and not (task.task_type == TaskType.chapter_generation and task.payload.get("rewrite"))
        ]
        return matches[-1] if matches else None

    def _latest_visible_asset(project_id: str, asset_type: AssetType, branch: str) -> Asset | None:
        assets = [asset for asset in state.store.list_assets(project_id, asset_type, branch=branch) if not asset.is_deleted and not _is_spot_fix_candidate(asset)]
        return assets[-1] if assets else None

    def _critical_stage_gate(output_assets: list[Asset]) -> dict:
        for asset in reversed(output_assets):
            if asset.asset_type in {AssetType.validation_report, AssetType.audit_report, AssetType.export_candidate}:
                data = asset.structured_data
                return {
                    "passed": data.get("passed"),
                    "ready": data.get("ready"),
                    "blocking_issues": data.get("blocking_issues", []),
                    "blocked_reason": data.get("blocked_reason", ""),
                    "improved": data.get("improved"),
                    "current_issue_count": data.get("current_issue_count"),
                    "previous_issue_count": data.get("previous_issue_count"),
                }
        return {}

    def _critical_stage_blocker(task: TaskRecord | None, output_assets: list[Asset], expected_types: tuple[AssetType, ...]) -> str:
        if task is None:
            return "任务尚未排队"
        if task.status in {TaskStatus.failed, TaskStatus.cancelled}:
            return task.error or f"任务状态为 {task.status.value}"
        if task.status == TaskStatus.completed:
            output_types = {asset.asset_type for asset in output_assets}
            missing = [asset_type.value for asset_type in expected_types if asset_type not in output_types]
            if missing:
                return "缺少阶段产物：" + "、".join(missing)
            if task.task_type == TaskType.export_candidate:
                candidate = next((asset for asset in output_assets if asset.asset_type == AssetType.export_candidate), None)
                if candidate is not None and not candidate.structured_data.get("ready"):
                    return str(candidate.structured_data.get("blocked_reason") or "导出候选未就绪")
        if task.parent_task_id:
            parent = state.store.get_task(task.parent_task_id)
            if parent is not None and parent.status != TaskStatus.completed:
                return f"等待上游任务 {parent.task_id}（{parent.status.value}）"
        return ""

    def _asset_order_key(asset: Asset) -> tuple[int, datetime, datetime]:
        return (asset.version, asset.updated_at, asset.created_at)

    def _source_chapter_ref(final_chapter: Asset) -> str:
        value = final_chapter.structured_data.get("source_chapter_ref")
        return value if isinstance(value, str) else ""

    def _final_chapter_stale_reason(final_chapter: Asset) -> str:
        chapter_number = _coerce_chapter_number(final_chapter.structured_data.get("chapter_number"))
        if chapter_number is None:
            return "定稿章节缺少有效章节号"
        source_ref = _source_chapter_ref(final_chapter)
        chapter_assets = [
            asset
            for asset in state.store.list_assets(final_chapter.project_id, AssetType.chapter, branch=final_chapter.branch)
            if not _is_spot_fix_candidate(asset)
            and _coerce_chapter_number(asset.structured_data.get("chapter_number")) == chapter_number
        ]
        source_asset = state.store.get_asset_by_id(source_ref) if source_ref else None
        if source_asset is not None and source_asset.is_deleted and source_asset.structured_data.get("accepted_spot_fix_candidate_id"):
            return f"第 {chapter_number} 章已接受定点修复，需重新复审并生成导出候选"
        visible_chapters = [asset for asset in chapter_assets if not asset.is_deleted]
        if visible_chapters:
            latest_chapter = max(visible_chapters, key=_asset_order_key)
            if latest_chapter.asset_id != source_ref:
                if latest_chapter.structured_data.get("accepted_spot_fix_candidate_id"):
                    return f"第 {chapter_number} 章已接受定点修复，需重新复审并生成导出候选"
                return f"第 {chapter_number} 章定稿后有新的章节版本，需重新复审并生成导出候选"
        deleted_accepted_spot_fixes = [
            asset
            for asset in chapter_assets
            if asset.is_deleted and asset.structured_data.get("accepted_spot_fix_candidate_id") and asset.asset_id != source_ref
        ]
        if deleted_accepted_spot_fixes:
            return f"第 {chapter_number} 章已接受定点修复，需重新复审并生成导出候选"
        return ""

    def _latest_final_chapter(project_id: str, branch: str, chapter_number: int) -> Asset | None:
        finals = [
            asset
            for asset in state.store.list_assets(project_id, AssetType.final_chapter, branch=branch)
            if not asset.is_deleted and _coerce_chapter_number(asset.structured_data.get("chapter_number")) == chapter_number
        ]
        return max(finals, key=_asset_order_key) if finals else None

    def _ready_export_candidate(project_id: str, branch: str) -> tuple[Asset | None, str]:
        candidates = [asset for asset in state.store.list_assets(project_id, AssetType.export_candidate, branch=branch) if not asset.is_deleted]
        if not candidates:
            return None, "尚未生成导出候选"
        candidate = max(candidates, key=_asset_order_key)
        tasks = state.store.list_tasks(project_id, branch=branch)
        creator = next((task for task in tasks if candidate.asset_id in task.output_refs), None)
        if creator is None or creator.task_type != TaskType.export_candidate or creator.status != TaskStatus.completed:
            return None, "导出候选不是系统关键路径产物"
        if not candidate.structured_data.get("ready"):
            return None, str(candidate.structured_data.get("blocked_reason") or "导出候选未就绪")
        refs = candidate.structured_data.get("final_chapter_refs", [])
        if not isinstance(refs, list) or not refs:
            return None, "导出候选缺少定稿章节引用"
        raw_chapter_numbers: list[int] = []
        for ref in refs:
            if not isinstance(ref, str):
                return None, "导出候选包含无效定稿章节引用"
            candidate_asset = state.store.get_asset_by_id(ref)
            if candidate_asset is None or candidate_asset.project_id != project_id or candidate_asset.branch != branch or candidate_asset.asset_type != AssetType.final_chapter or candidate_asset.is_deleted:
                return None, "导出候选引用的定稿章节不存在"
            raw_chapter_number = _coerce_chapter_number(candidate_asset.structured_data.get("chapter_number"))
            if raw_chapter_number is None:
                return None, "定稿章节缺少有效章节号"
            raw_chapter_numbers.append(raw_chapter_number)
        duplicate_chapters = sorted({number for number in raw_chapter_numbers if raw_chapter_numbers.count(number) > 1})
        if duplicate_chapters:
            return None, "导出候选包含重复定稿章节：" + "、".join(str(number) for number in duplicate_chapters)
        chapter_numbers: list[int] = []
        for ref in refs:
            if not isinstance(ref, str):
                return None, "导出候选包含无效定稿章节引用"
            asset = state.store.get_asset_by_id(ref)
            if asset is None or asset.project_id != project_id or asset.branch != branch or asset.asset_type != AssetType.final_chapter or asset.is_deleted:
                return None, "导出候选引用的定稿章节不存在"
            final_creator = next((task for task in tasks if asset.asset_id in task.output_refs), None)
            if final_creator is None or final_creator.task_type != TaskType.final_save or final_creator.status != TaskStatus.completed:
                return None, "定稿章节不是系统关键路径产物"
            artifact_issues = find_production_artifact_issues(asset.content)
            if artifact_issues:
                return None, "定稿章节包含内部写作痕迹：" + "；".join(artifact_issues)
            stale_reason = _final_chapter_stale_reason(asset)
            if stale_reason:
                return None, stale_reason
            chapter_number = _coerce_chapter_number(asset.structured_data.get("chapter_number"))
            if chapter_number is None:
                return None, "定稿章节缺少有效章节号"
            latest_final = _latest_final_chapter(project_id, branch, chapter_number)
            if latest_final is None or latest_final.asset_id != asset.asset_id:
                return None, f"第 {chapter_number} 章已有更新的定稿章节，需重新生成导出候选"
            chapter_numbers.append(chapter_number)
        target_chapter = _coerce_chapter_number(candidate.structured_data.get("chapter_number")) or max(chapter_numbers)
        expected = list(range(1, target_chapter + 1))
        if sorted(chapter_numbers) != expected:
            missing = [number for number in expected if number not in chapter_numbers]
            return None, "缺少连续定稿章节：" + "、".join(str(number) for number in missing)
        return candidate, ""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state.runtime.start()
        try:
            yield
        finally:
            state.runtime.stop()
            state.store.close()

    app = FastAPI(title="StoryForge API", version="0.1.0", lifespan=lifespan)
    app.extra["storyforge_store"] = state.store

    # Rate limiter: 300 requests per minute per client
    rate_limiter = RateLimiter(max_requests=300, window_seconds=60)

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_host = request.client.host if request.client else "unknown"
        allowed, remaining = rate_limiter.is_allowed(client_host)
        if not allowed:
            return Response(
                content=json.dumps({"detail": "Rate limit exceeded. Try again later."}),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    # --- Authentication endpoints ---

    @app.post("/api/auth/token")
    def generate_auth_token(body: GenerateTokenRequest = Body(), authorization: str | None = Header(default=None)) -> dict[str, str]:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        actor_id = token_auth.verify(authorization[len("Bearer "):])
        if actor_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        if body.user_id != actor_id:
            raise HTTPException(status_code=403, detail="Cannot mint token for a different user")
        token = token_auth.generate_token(body.user_id)
        return {"token": token, "user_id": body.user_id}

    @app.delete("/api/auth/token")
    def revoke_auth_token(body: RevokeTokenRequest = Body(), authorization: str | None = Header(default=None)) -> dict[str, str]:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        caller_token = authorization[len("Bearer "):]
        actor_id = token_auth.verify(caller_token)
        if actor_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        token_owner = token_auth.verify(body.token)
        if token_owner is None:
            raise HTTPException(status_code=404, detail="Token not found")
        if token_owner != actor_id:
            raise HTTPException(status_code=403, detail="Cannot revoke token for a different user")
        revoked = token_auth.revoke_token(body.token)
        if not revoked:
            raise HTTPException(status_code=404, detail="Token not found")
        return {"message": "Token revoked"}

    @app.get("/api/auth/me")
    def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        token = authorization[len("Bearer "):]
        user_id = token_auth.verify(token)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return {"user_id": user_id}

    @app.get("/", response_class=HTMLResponse)
    def frontend_index() -> HTMLResponse:
        if not FRONTEND_INDEX_PATH.is_file():
            raise HTTPException(status_code=503, detail="Workbench frontend not available")
        return HTMLResponse(FRONTEND_INDEX_PATH.read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "storyforge"}

    @app.post("/api/projects", response_model=Project)
    def create_project(body: CreateProjectRequest, authorization: str | None = Header(default=None)) -> Project:
        project = Project(
            idea=body.idea,
            title=body.title,
            genre=body.genre,
            tags=body.tags,
            audience=body.audience,
            target_length=body.target_length,
            owner_id=_actor_from_authorization(authorization) or "",
        )
        return state.store.create_project(project)

    @app.get("/api/projects", response_model=list[Project])
    def list_projects(authorization: str | None = Header(default=None)) -> list[Project]:
        actor_id = _actor_from_authorization(authorization)
        return [project for project in state.store.list_projects() if _actor_can_read_project(project, actor_id)]

    @app.get("/api/projects/{project_id}", response_model=Project)
    def get_project(project_id: str, authorization: str | None = Header(default=None)) -> Project:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        return project

    @app.patch("/api/projects/{project_id}", response_model=Project)
    def update_project(project_id: str, body: UpdateProjectRequest, authorization: str | None = Header(default=None)) -> Project:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        update_data = body.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(project, field, value)
        from storyforge.domain.models import utc_now
        project.updated_at = utc_now()
        return state.store.save_project(project)

    # --- Role-based access control ---

    def _require_write_access(project: Project, actor_id: str | None) -> None:
        """Reject viewer role. No actor_id = author (full access)."""
        if actor_id is None:
            return  # Unauthenticated requests are treated as author for backwards compat
        collaborator = next((c for c in project.collaborators if c.actor_id == actor_id), None)
        if collaborator is None:
            return  # Not in collaborator list = author
        if collaborator.role == "viewer":
            raise HTTPException(status_code=403, detail="Viewer role cannot write")

    def _require_read_access(project: Project, actor_id: str | None) -> None:
        if actor_id is None or not project.collaborators:
            return
        if not any(c.actor_id == actor_id for c in project.collaborators):
            raise HTTPException(status_code=403, detail="Actor cannot read this project")

    def _actor_from_authorization(authorization: str | None) -> str | None:
        if authorization is None:
            return None
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        actor_id = token_auth.verify(authorization[len("Bearer "):])
        if actor_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return actor_id

    def _project_requires_token(project: Project) -> bool:
        return bool(project.owner_id or project.collaborators)

    def _actor_can_read_project(project: Project, actor_id: str | None) -> bool:
        if project.owner_id:
            return actor_id == project.owner_id or any(c.actor_id == actor_id for c in project.collaborators)
        if not project.collaborators:
            return True
        return actor_id is not None and any(c.actor_id == actor_id for c in project.collaborators)

    def _actor_can_write_project(project: Project, actor_id: str | None) -> bool:
        if project.owner_id and actor_id == project.owner_id:
            return True
        if not project.collaborators:
            return project.owner_id == ""
        collaborator = next((c for c in project.collaborators if c.actor_id == actor_id), None)
        return collaborator is not None and collaborator.role != "viewer"

    def _require_runtime_scope_read_access(project_id: str | None, authorization: str | None) -> str | None:
        if project_id is not None:
            return _require_token_read_access(_project_or_404(project_id), authorization)
        protected_projects = [project for project in state.store.list_projects() if _project_requires_token(project)]
        if not protected_projects:
            return _actor_from_authorization(authorization)
        actor_id = _actor_from_authorization(authorization)
        if actor_id is None:
            raise HTTPException(status_code=403, detail="Authentication token required")
        return actor_id

    def _require_runtime_scope_write_access(project_id: str | None, authorization: str | None) -> str | None:
        if project_id is not None:
            return _require_token_write_access(_project_or_404(project_id), authorization)
        protected_projects = [project for project in state.store.list_projects() if _project_requires_token(project)]
        if not protected_projects:
            return _actor_from_authorization(authorization)
        actor_id = _actor_from_authorization(authorization)
        if actor_id is None:
            raise HTTPException(status_code=403, detail="Authentication token required")
        if not all(_actor_can_write_project(project, actor_id) for project in protected_projects):
            raise HTTPException(status_code=403, detail="Actor cannot write all protected projects")
        return actor_id

    def _require_token_read_access(project: Project, authorization: str | None) -> str | None:
        actor_id = _actor_from_authorization(authorization)
        if project.owner_id:
            if actor_id is None:
                raise HTTPException(status_code=403, detail="Authentication token required")
            if actor_id == project.owner_id or any(c.actor_id == actor_id for c in project.collaborators):
                return actor_id
            raise HTTPException(status_code=403, detail="Actor cannot read this project")
        if not project.collaborators:
            return actor_id
        if actor_id is None:
            raise HTTPException(status_code=403, detail="Authentication token required")
        if not any(c.actor_id == actor_id for c in project.collaborators):
            raise HTTPException(status_code=403, detail="Actor cannot read this project")
        return actor_id

    def _require_token_write_access(project: Project, authorization: str | None) -> str | None:
        actor_id = _actor_from_authorization(authorization)
        if project.owner_id:
            if actor_id is None:
                raise HTTPException(status_code=403, detail="Authentication token required")
            if actor_id == project.owner_id:
                return actor_id
            collaborator = next((c for c in project.collaborators if c.actor_id == actor_id), None)
            if collaborator is None:
                raise HTTPException(status_code=403, detail="Actor cannot write this project")
            if collaborator.role == "viewer":
                raise HTTPException(status_code=403, detail="Viewer role cannot write")
            return actor_id
        if not project.collaborators:
            return actor_id
        if actor_id is None:
            raise HTTPException(status_code=403, detail="Authentication token required")
        collaborator = next((c for c in project.collaborators if c.actor_id == actor_id), None)
        if collaborator is None:
            raise HTTPException(status_code=403, detail="Actor cannot write this project")
        if collaborator.role == "viewer":
            raise HTTPException(status_code=403, detail="Viewer role cannot write")
        return actor_id

    def _require_token_author_access(project: Project, authorization: str | None) -> str | None:
        actor_id = _actor_from_authorization(authorization)
        if project.owner_id:
            if actor_id is None:
                raise HTTPException(status_code=403, detail="Authentication token required")
            if actor_id != project.owner_id:
                raise HTTPException(status_code=403, detail="Only author can manage collaborators")
            return actor_id
        if not project.collaborators:
            return actor_id
        if actor_id is None:
            raise HTTPException(status_code=403, detail="Authentication token required")
        raise HTTPException(status_code=403, detail="Only author can manage collaborators")

    def _require_task_read_access(task: TaskRecord, authorization: str | None) -> None:
        project = _project_or_404(task.project_id)
        _require_token_read_access(project, authorization)

    def _require_task_write_access(task: TaskRecord, authorization: str | None) -> None:
        project = _project_or_404(task.project_id)
        _require_token_write_access(project, authorization)

    def _require_author_access(project: Project, actor_id: str | None) -> None:
        """Only author can manage collaborators. No actor_id = author."""
        if actor_id is None:
            return
        collaborator = next((c for c in project.collaborators if c.actor_id == actor_id), None)
        if collaborator is not None:
            raise HTTPException(status_code=403, detail="Only author can manage collaborators")

    @app.get("/api/projects/{project_id}/collaborators", response_model=list[CollaboratorRecord])
    def list_project_collaborators(project_id: str, authorization: str | None = Header(default=None)) -> list[CollaboratorRecord]:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        return project.collaborators

    @app.post("/api/projects/{project_id}/collaborators", response_model=Project)
    def add_project_collaborator(project_id: str, body: CollaboratorRecord = Body(), authorization: str | None = Header(default=None)) -> Project:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_author_access(project, authorization)
        project.collaborators = [item for item in project.collaborators if item.actor_id != body.actor_id]
        project.collaborators.append(body)
        project.updated_at = utc_now()
        return state.store.save_project(project)

    @app.get("/api/projects/{project_id}/summary", response_model=ProjectExecutionSummary)
    def get_project_summary(project_id: str, authorization: str | None = Header(default=None)) -> ProjectExecutionSummary:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        return state.application.summarize_project(project_id)

    @app.get("/api/projects/{project_id}/quality-experiments", response_model=QualityExperimentListResponse)
    def get_quality_experiments(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> QualityExperimentListResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        experiment_map: dict[tuple[str, str], QualityExperimentMetric] = {}
        key_by_review_asset_id: dict[str, tuple[str, str]] = {}
        keys_by_chapter_ref: dict[str, set[tuple[str, str]]] = {}
        keys_by_chapter_number: dict[int, set[tuple[str, str]]] = {}
        for task in state.store.list_tasks(project_id, branch=branch):
            if task.task_type != TaskType.chapter_review:
                continue
            values = task.effective_config_snapshot.values
            experiment = values.get("quality_experiment")
            variant = values.get("quality_variant")
            if not experiment or not variant:
                continue
            key = (str(experiment), str(variant))
            metric = experiment_map.setdefault(key, QualityExperimentMetric(experiment=key[0], variant=key[1]))
            metric.review_count += 1
            for output_ref in task.output_refs:
                key_by_review_asset_id[output_ref] = key
            chapter_ref = next(
                (
                    ref_id
                    for ref_id in task.input_asset_refs
                    if (asset := state.store.get_asset_by_id(ref_id)) is not None and asset.asset_type == AssetType.chapter
                ),
                None,
            )
            if chapter_ref is not None:
                keys_by_chapter_ref.setdefault(chapter_ref, set()).add(key)
            chapter_number = _coerce_chapter_number(task.payload.get("chapter_number"))
            if chapter_number is not None:
                keys_by_chapter_number.setdefault(chapter_number, set()).add(key)
            usage = values.get("llm_usage", {})
            if isinstance(usage, dict):
                metric.input_tokens += _coerce_usage_count(usage.get("input_tokens", 0))
                metric.output_tokens += _coerce_usage_count(usage.get("output_tokens", 0))
                metric.total_tokens += _coerce_usage_count(usage.get("total_tokens", 0))
        for review in state.store.list_assets(project_id, AssetType.review_note, branch=branch):
            if review.is_deleted:
                continue
            data = review.structured_data
            experiment = data.get("quality_experiment")
            variant = data.get("quality_variant")
            key: tuple[str, str] | None = (str(experiment), str(variant)) if experiment and variant else None
            if key is None:
                key = key_by_review_asset_id.get(review.asset_id)
            if key is None:
                chapter_ref = data.get("chapter_ref")
                if isinstance(chapter_ref, str) and chapter_ref:
                    candidate_keys = keys_by_chapter_ref.get(chapter_ref, set())
                    if len(candidate_keys) == 1:
                        key = next(iter(candidate_keys))
            if key is None:
                chapter_number = _coerce_chapter_number(data.get("chapter_number"))
                if chapter_number is not None:
                    candidate_keys = keys_by_chapter_number.get(chapter_number, set())
                    if len(candidate_keys) == 1:
                        key = next(iter(candidate_keys))
            if key is None:
                continue
            metric = experiment_map.setdefault(key, QualityExperimentMetric(experiment=key[0], variant=key[1]))
            approved = data.get("approved")
            if approved is True:
                metric.approved_count += 1
            elif approved is False:
                metric.rewrite_count += 1
            issues = data.get("issues", [])
            if isinstance(issues, list):
                metric.issue_count += len(issues)
            failed_checks = data.get("failed_checks", [])
            if isinstance(failed_checks, list):
                for check in failed_checks:
                    if isinstance(check, str) and check:
                        metric.failed_check_distribution[check] = metric.failed_check_distribution.get(check, 0) + 1
        experiments = sorted(experiment_map.values(), key=lambda item: (item.experiment, item.variant))
        for metric in experiments:
            if metric.review_count > 0:
                metric.approval_rate = metric.approved_count / metric.review_count
                metric.rewrite_rate = metric.rewrite_count / metric.review_count
                metric.average_issue_count = metric.issue_count / metric.review_count
        return QualityExperimentListResponse(project_id=project_id, branch=branch, experiments=experiments)

    @app.get("/api/projects/{project_id}/production-console", response_model=ProductionConsoleResponse)
    def get_production_console(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> ProductionConsoleResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        tasks = state.store.list_tasks(project_id, branch=branch)
        task_summary = TaskSummary(total=len(tasks))
        for task in tasks:
            setattr(task_summary, task.status.value, getattr(task_summary, task.status.value) + 1)
        return ProductionConsoleResponse(
            project_id=project_id,
            branch=branch,
            auto_mode=project.auto_mode,
            task_summary=task_summary,
            queue=tasks,
            available_actions=["queue_first_loop", "process_next", "drain"],
        )

    @app.get("/api/projects/{project_id}/critical-path")
    def get_critical_path(project_id: str, branch: str = "main", chapter_number: int = 1, authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        tasks = state.store.list_tasks(project_id, branch=branch)
        stages: list[dict] = []
        for stage_name, task_type, expected_types in CRITICAL_PATH_STAGES:
            task = _critical_path_task(tasks, task_type, chapter_number)
            input_assets = _task_assets(task, task.input_asset_refs) if task is not None else []
            output_assets = _task_assets(task, task.output_refs) if task is not None else []
            stages.append(
                {
                    "stage": stage_name,
                    "task_type": task_type.value,
                    "status": task.status.value if task is not None else "missing",
                    "task": _task_summary(task) if task is not None else None,
                    "input_assets": [_asset_summary(asset) for asset in input_assets],
                    "output_assets": [_asset_summary(asset) for asset in output_assets],
                    "gate": _critical_stage_gate(output_assets),
                    "blocked_reason": _critical_stage_blocker(task, output_assets, expected_types),
                }
            )
        bootstrap_assets = {
            asset_type.value: (_asset_summary(asset) if (asset := _latest_visible_asset(project_id, asset_type, branch)) is not None else None)
            for asset_type in BOOTSTRAP_ASSET_TYPES
        }
        export_candidate, export_blocked_reason = _ready_export_candidate(project_id, branch)
        next_stage = next((stage for stage in stages if stage["status"] != TaskStatus.completed.value), None)
        first_blocked = next((stage for stage in stages if stage["blocked_reason"]), None)
        return {
            "project_id": project_id,
            "branch": branch,
            "chapter_number": chapter_number,
            "critical_path": stages,
            "bootstrap_assets": bootstrap_assets,
            "bootstrap_complete": all(asset is not None for asset in bootstrap_assets.values()),
            "export_ready": export_candidate is not None,
            "export_candidate": _asset_summary(export_candidate) if export_candidate is not None else None,
            "export_blocked_reason": export_blocked_reason,
            "next_step": first_blocked["blocked_reason"] if first_blocked is not None else (next_stage["stage"] if next_stage is not None else "ready"),
        }

    @app.patch("/api/projects/{project_id}/auto-mode", response_model=Project)
    def toggle_auto_mode(project_id: str, body: AutoModeToggle, authorization: str | None = Header(default=None)) -> Project:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        project.auto_mode = body.auto_mode
        project.updated_at = utc_now()
        return state.store.save_project(project)

    @app.get("/api/projects/{project_id}/events")
    def stream_project_events(
        project_id: str,
        max_events: int | None = None,
        branch: str | None = None,
        after_event_id: str | None = None,
        after_timestamp: str | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch) if branch is not None else None

        after_event_id = after_event_id or last_event_id
        after_datetime: datetime | None = None
        if after_timestamp is not None:
            try:
                after_datetime = datetime.fromisoformat(after_timestamp)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="after_timestamp must be a valid ISO 8601 datetime") from exc

        def encode_event(event: EventRecord) -> str:
            payload = event.model_dump(mode="json")
            return f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {json.dumps(payload)}\n\n"

        def event_matches_branch(event: EventRecord) -> bool:
            if branch is None:
                return True
            event_branch = event.payload.get("branch")
            if isinstance(event_branch, str):
                return event_branch == branch
            if event.task_id:
                task = state.store.get_task(event.task_id)
                return task is not None and task.branch == branch
            return False

        def event_stream():
            emitted = 0
            backlog: list[EventRecord] = []
            replay_enabled = after_event_id is None
            for task in state.store.list_tasks(project_id, branch=branch):
                backlog.extend(state.store.list_events(task.task_id))
            if branch is None:
                backlog.extend(event for event in state.store.list_events("") if event.project_id == project_id)
            else:
                backlog.extend(event for event in state.store.list_events("") if event.project_id == project_id and event_matches_branch(event))
            backlog.sort(key=lambda event: (event.timestamp, event.event_id))
            for event in backlog:
                if after_event_id is not None:
                    if replay_enabled:
                        pass
                    elif event.event_id == after_event_id:
                        replay_enabled = True
                        continue
                    else:
                        continue
                elif after_datetime is not None and event.timestamp <= after_datetime:
                    continue
                yield encode_event(event)
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return

            channel, unsubscribe = state.event_bus.subscribe(project_id)
            try:
                while True:
                    try:
                        event = channel.get(timeout=5.0)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    if after_datetime is not None and event.timestamp <= after_datetime:
                        continue
                    if not event_matches_branch(event):
                        continue
                    yield encode_event(event)
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        break
            finally:
                unsubscribe()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/projects/{project_id}/timeline", response_model=TimelineListResponse)
    def list_timeline(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> TimelineListResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        items = [_timeline_asset_to_item(asset) for asset in _latest_timeline_assets(project_id, branch)]
        return TimelineListResponse(items=items, total=len(items))

    @app.post("/api/projects/{project_id}/timeline", response_model=TimelineItem)
    def create_timeline_event(project_id: str, body: TimelineCreateRequest, authorization: str | None = Header(default=None)) -> TimelineItem:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        asset = Asset(
            project_id=project_id,
            asset_type=AssetType.timeline,
            branch=branch,
            content=body.description,
            source="human",
            structured_data={
                "chapter_number": body.chapter_number,
                "title": body.title,
                "description": body.description,
                "story_time_label": body.story_time_label,
                "order_index": body.order_index,
                "characters": body.characters,
                "location": body.location,
            },
        )
        saved = state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, saved.asset_id, saved.asset_type.value, "created_timeline_event", branch=saved.branch)
        return _timeline_asset_to_item(saved)

    @app.patch("/api/projects/{project_id}/timeline/{timeline_event_id}", response_model=TimelineItem)
    def update_timeline_event(project_id: str, timeline_event_id: str, body: TimelineUpdateRequest, authorization: str | None = Header(default=None)) -> TimelineItem:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        asset = _get_timeline_asset(project_id, timeline_event_id, branch)
        update_data = body.model_dump(exclude_unset=True)
        update_data.pop("branch", None)
        structured_data = dict(asset.structured_data)
        structured_data.update(update_data)
        updated = Asset(
            asset_id=asset.asset_id,
            project_id=project_id,
            asset_type=AssetType.timeline,
            branch=branch,
            content=str(structured_data.get("description") or asset.content or ""),
            source="human",
            structured_data=structured_data,
            comments=list(asset.comments or []),
        )
        saved = state.store.save_asset(updated, lineage_origin_id=asset.asset_id)
        _emit_asset_updated_event(project_id, saved.asset_id, saved.asset_type.value, "updated_timeline_event", branch=saved.branch)
        return _timeline_asset_to_item(saved)

    @app.delete("/api/projects/{project_id}/timeline/{timeline_event_id}")
    def delete_timeline_event(project_id: str, timeline_event_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, branch)
        asset = _get_timeline_asset(project_id, timeline_event_id, branch)
        asset.is_deleted = True
        asset.deleted_at = utc_now()
        asset.updated_at = utc_now()
        state.store.save_asset(asset, lineage_origin_id=asset.asset_id)
        _emit_asset_updated_event(project_id, asset.asset_id, asset.asset_type.value, "deleted_timeline_event", branch=asset.branch)
        return {"deleted": True, "timeline_event_id": timeline_event_id, "branch": branch}

    @app.get("/api/projects/{project_id}/foreshadowing", response_model=ForeshadowingListResponse)
    def list_foreshadowing(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> ForeshadowingListResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        items = [
            _foreshadowing_asset_to_item(asset)
            for asset in state.store.list_assets(project_id, AssetType.continuity_note, branch=branch)
            if not asset.is_deleted and asset.structured_data.get("kind") == "foreshadowing"
        ]
        return ForeshadowingListResponse(items=items, total=len(items))

    @app.post("/api/projects/{project_id}/foreshadowing", response_model=ForeshadowingItem)
    def create_foreshadowing(project_id: str, body: ForeshadowingCreateRequest, authorization: str | None = Header(default=None)) -> ForeshadowingItem:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        asset = Asset(
            project_id=project_id,
            asset_type=AssetType.continuity_note,
            branch=branch,
            content=body.description,
            source="human",
            structured_data={
                "kind": "foreshadowing",
                "title": body.title,
                "description": body.description,
                "introduced_chapter": body.introduced_chapter,
                "expected_resolution_chapter": body.expected_resolution_chapter,
                "status": "planted",
            },
        )
        saved = state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, saved.asset_id, saved.asset_type.value, "created_foreshadowing", branch=saved.branch)
        return _foreshadowing_asset_to_item(saved)

    @app.patch("/api/projects/{project_id}/foreshadowing/{foreshadowing_id}", response_model=ForeshadowingItem)
    def update_foreshadowing(project_id: str, foreshadowing_id: str, body: ForeshadowingUpdateRequest, authorization: str | None = Header(default=None)) -> ForeshadowingItem:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        asset = _get_foreshadowing_asset(project_id, foreshadowing_id, branch)
        data = asset.structured_data
        update_data = body.model_dump(exclude_unset=True)
        update_data.pop("branch", None)
        for key, value in update_data.items():
            data[key] = value
        if "description" in update_data:
            asset.content = str(update_data["description"] or "")
        asset.updated_at = utc_now()
        saved = state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, saved.asset_id, saved.asset_type.value, "updated_foreshadowing", branch=saved.branch)
        return _foreshadowing_asset_to_item(saved)

    @app.delete("/api/projects/{project_id}/foreshadowing/{foreshadowing_id}")
    def delete_foreshadowing(project_id: str, foreshadowing_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, branch)
        asset = _get_foreshadowing_asset(project_id, foreshadowing_id, branch)
        asset.is_deleted = True
        asset.deleted_at = utc_now()
        asset.updated_at = utc_now()
        state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, asset.asset_id, asset.asset_type.value, "deleted_foreshadowing", branch=asset.branch)
        return {"deleted": True, "foreshadowing_id": foreshadowing_id, "branch": branch}

    @app.get("/api/projects/{project_id}/assets", response_model=list[Asset])
    def list_project_assets(project_id: str, branch: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> list[Asset]:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_branch_for_project(project_id, branch)
        return [asset for asset in state.store.list_assets(project_id, branch=branch) if not asset.is_deleted and not _is_spot_fix_candidate(asset)]

    @app.get("/api/projects/{project_id}/assets/trash", response_model=list[Asset])
    def list_project_trash(project_id: str, branch: str = Query(default="main"), authorization: str | None = Header(default=None)) -> list[Asset]:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        return [asset for asset in state.store.list_trash(project_id) if asset.branch == branch]

    @app.get("/api/projects/{project_id}/assets/{asset_type}", response_model=Asset)
    def get_latest_asset(project_id: str, asset_type: AssetType, branch: str = "main", authorization: str | None = Header(default=None)) -> Asset:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        assets = [asset for asset in state.store.list_assets(project_id, asset_type, branch=branch) if not asset.is_deleted]
        if asset_type == AssetType.chapter:
            assets = [asset for asset in assets if not _is_spot_fix_candidate(asset)]
        if not assets:
            raise HTTPException(status_code=404, detail="Asset not found")
        return assets[-1]

    @app.get("/api/projects/{project_id}/assets/{asset_id}/lineage")
    def get_asset_lineage(project_id: str, asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        asset = state.store.get_asset_by_id(asset_id)
        if asset is None or asset.project_id != project_id or asset.branch != branch or asset.is_deleted:
            raise HTTPException(status_code=404, detail="Asset not found")
        tasks = state.store.list_tasks(project_id, branch=branch)
        created_by = next((task for task in tasks if asset.asset_id in task.output_refs), None)
        upstream_assets = _task_assets(created_by, created_by.input_asset_refs) if created_by is not None else []
        downstream_tasks = [task for task in tasks if asset.asset_id in task.input_asset_refs]
        lineage_members = _asset_lineage_members(project_id, asset)
        return {
            "project_id": project_id,
            "branch": branch,
            "asset": _asset_summary(asset),
            "created_by_task": _task_summary(created_by) if created_by is not None else None,
            "frozen_inputs": [_asset_summary(input_asset) for input_asset in upstream_assets],
            "upstream_asset_ids": [input_asset.asset_id for input_asset in upstream_assets],
            "downstream_tasks": [_task_summary(task) for task in downstream_tasks],
            "lineage_asset_ids": [member.asset_id for member in lineage_members],
        }

    @app.get("/api/projects/{project_id}/pacing-curve", response_model=PacingCurveResponse)
    def get_pacing_curve(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> PacingCurveResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        reviews_by_chapter: dict[int, list[Asset]] = {}
        for review in state.store.list_assets(project_id, AssetType.review_note, branch=branch):
            if review.is_deleted:
                continue
            chapter_number = _coerce_chapter_number(review.structured_data.get("chapter_number"))
            if chapter_number is None:
                continue
            reviews_by_chapter.setdefault(chapter_number, []).append(review)
        chapters_by_number: dict[int, Asset] = {}
        for chapter in state.store.list_assets(project_id, AssetType.chapter, branch=branch):
            if chapter.is_deleted or _is_spot_fix_candidate(chapter):
                continue
            chapter_number = _coerce_chapter_number(chapter.structured_data.get("chapter_number"))
            if chapter_number is None:
                continue
            existing = chapters_by_number.get(chapter_number)
            if existing is None or (chapter.version, chapter.updated_at, chapter.created_at) > (existing.version, existing.updated_at, existing.created_at):
                chapters_by_number[chapter_number] = chapter
        points: list[PacingCurvePoint] = []
        for chapter_number, chapter in chapters_by_number.items():
            matching_reviews = [review for review in reviews_by_chapter.get(chapter_number, []) if _review_matches_chapter_revision(review, chapter)]
            current_review = sorted(matching_reviews, key=lambda review: (review.version, review.updated_at, review.created_at))[-1] if matching_reviews else None
            quality_details = current_review.structured_data.get("quality_details", {}) if current_review else {}
            pacing_details = quality_details.get("pacing", {}) if isinstance(quality_details, dict) else {}
            values = pacing_details.get("details", {}) if isinstance(pacing_details, dict) else {}
            pacing_score, conflict_intensity, emotional_intensity = _pacing_metrics(values)
            points.append(
                PacingCurvePoint(
                    chapter_number=chapter_number,
                    title=str(chapter.structured_data.get("title") or ""),
                    pacing_score=pacing_score,
                    conflict_intensity=conflict_intensity,
                    emotional_intensity=emotional_intensity,
                    status="known" if current_review is not None and pacing_score is not None else "unknown",
                    review_asset_id=current_review.asset_id if current_review else None,
                )
            )
        points.sort(key=lambda point: point.chapter_number)
        return PacingCurveResponse(project_id=project_id, branch=branch, points=points)

    def _chapter_has_prior_revision(chapter: Asset) -> bool:
        return any(
            candidate.asset_id == chapter.asset_id and candidate.version < chapter.version
            for candidate in state.store.list_assets(chapter.project_id, AssetType.chapter, branch=chapter.branch)
        )

    def _review_matches_chapter_revision(review: Asset | None, chapter: Asset) -> bool:
        if review is None:
            return False
        chapter_ref = review.structured_data.get("chapter_ref")
        chapter_version = _coerce_chapter_number(review.structured_data.get("chapter_version"))
        if chapter_version is not None:
            if isinstance(chapter_ref, str) and chapter_ref and chapter_ref != chapter.asset_id:
                return False
            return chapter_version == chapter.version
        if isinstance(chapter_ref, str) and chapter_ref:
            return chapter_ref == chapter.asset_id and not _chapter_has_prior_revision(chapter) and review.updated_at >= chapter.updated_at
        return not _chapter_has_prior_revision(chapter) and review.updated_at >= chapter.updated_at

    @app.get("/api/projects/{project_id}/reviews/latest", response_model=Asset)
    def get_latest_review(project_id: str, branch: str = "main", chapter_number: int | None = None, authorization: str | None = Header(default=None)) -> Asset:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        reviews = state.store.list_assets(project_id, AssetType.review_note, branch=branch)
        if chapter_number is not None:
            reviews = [review for review in reviews if _coerce_chapter_number(review.structured_data.get("chapter_number")) == chapter_number]
        reviews = [review for review in reviews if not review.is_deleted]
        if not reviews:
            raise HTTPException(status_code=404, detail="Review not found")
        return sorted(reviews, key=lambda review: (review.version, review.updated_at, review.created_at))[-1]

    @app.post("/api/projects/{project_id}/assets", response_model=Asset)
    def create_project_asset(project_id: str, body: CreateAssetRequest = Body(), authorization: str | None = Header(default=None)) -> Asset:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        if body.asset_type in SYSTEM_GATE_ASSET_TYPES:
            raise HTTPException(status_code=403, detail="System gate assets must be produced by workflow tasks")
        asset = Asset(
            project_id=project_id,
            asset_type=body.asset_type,
            content=body.content,
            source=body.source or "human",
            structured_data=body.structured_data,
        )
        if _is_foreshadowing_asset(asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        result = state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, result.asset_id, body.asset_type.value, "created", branch=result.branch)
        return result

    @app.put("/api/projects/{project_id}/assets/{asset_id}", response_model=Asset)
    def update_project_asset(project_id: str, asset_id: str, body: UpdateAssetRequest = Body(), authorization: str | None = Header(default=None)) -> Asset:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        branch = _require_project_branch(project, body.branch)
        existing_asset = state.store.get_asset_by_id(asset_id)
        if existing_asset is None or existing_asset.project_id != project_id or existing_asset.branch != branch or existing_asset.is_deleted:
            raise HTTPException(status_code=404, detail="Asset not found")
        if _is_foreshadowing_asset(existing_asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        if existing_asset.asset_type in SYSTEM_GATE_ASSET_TYPES:
            raise HTTPException(status_code=403, detail="System gate assets are read-only")
        actor_id = _require_token_write_access(project, authorization)
        lineage_origin_id = str(existing_asset.structured_data.get("origin_asset_id") or existing_asset.asset_id)
        structured_data = dict(existing_asset.structured_data)
        structured_data.update(body.structured_data)
        structured_data["origin_asset_id"] = lineage_origin_id
        if actor_id:
            structured_data["actor_id"] = actor_id
        elif body.actor_id:
            structured_data["actor_id"] = body.actor_id
        if body.actor_name:
            structured_data["actor_name"] = body.actor_name
        if body.role:
            structured_data["role"] = body.role
        if body.approval_status:
            structured_data["approval_status"] = body.approval_status
        if body.change_note:
            structured_data["change_note"] = body.change_note
        asset = Asset(
            project_id=project_id,
            asset_type=existing_asset.asset_type,
            branch=existing_asset.branch,
            content=body.content,
            source=body.source or "human",
            structured_data=structured_data,
            comments=list(existing_asset.comments or []),
        )
        if _is_foreshadowing_asset(asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        try:
            result = state.store.save_asset(asset, expected_version=body.base_version, lineage_origin_id=lineage_origin_id)
            _emit_asset_updated_event(project_id, result.asset_id, existing_asset.asset_type.value, "updated", branch=result.branch)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/assets/{asset_id}/rollback", response_model=Asset)
    def rollback_asset_version(
        project_id: str,
        asset_id: str,
        body: RollbackAssetRequest,
        authorization: str | None = Header(default=None),
    ) -> Asset:
        """Rollback: create a new version based on a target version's content and structured_data."""
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        branch = _require_project_branch(project, body.branch)
        existing_asset = state.store.get_asset_by_id(asset_id)
        if existing_asset is None or existing_asset.project_id != project_id or existing_asset.branch != branch or existing_asset.is_deleted:
            raise HTTPException(status_code=404, detail="Asset not found")
        if _is_foreshadowing_asset(existing_asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        if existing_asset.asset_type in SYSTEM_GATE_ASSET_TYPES:
            raise HTTPException(status_code=403, detail="System gate assets are read-only")
        actor_id = _require_token_write_access(project, authorization)

        target_version = body.target_version
        if target_version is None:
            raise HTTPException(status_code=400, detail="target_version is required")

        lineage_origin_id = str(existing_asset.structured_data.get("origin_asset_id") or existing_asset.asset_id)
        versions = state.store.list_asset_versions(project_id, existing_asset.asset_type, branch=existing_asset.branch)
        target = next(
            (
                v
                for v in versions
                if v.version == target_version
                and v.branch == existing_asset.branch
                and not v.is_deleted
                and (v.asset_id == lineage_origin_id or v.structured_data.get("origin_asset_id") == lineage_origin_id)
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail=f"Version {target_version} not found")
        if _is_foreshadowing_asset(target):
            raise HTTPException(status_code=404, detail="Asset not found")

        structured_data = dict(target.structured_data)
        structured_data["origin_asset_id"] = lineage_origin_id
        structured_data["rollback_from_version"] = target_version
        if actor_id:
            structured_data["actor_id"] = actor_id

        asset = Asset(
            project_id=project_id,
            asset_type=existing_asset.asset_type,
            branch=existing_asset.branch,
            content=target.content,
            source="human",
            structured_data=structured_data,
        )
        result = state.store.save_asset(asset, lineage_origin_id=lineage_origin_id)
        _emit_asset_updated_event(project_id, result.asset_id, existing_asset.asset_type.value, "rolled_back", branch=result.branch)
        return result

    @app.delete("/api/projects/{project_id}/assets/{asset_id}", response_model=dict)
    def delete_project_asset(project_id: str, asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        branch = _require_project_branch(project, branch)
        existing_asset = state.store.get_asset_by_id(asset_id)
        if existing_asset is None or existing_asset.project_id != project_id or existing_asset.branch != branch:
            raise HTTPException(status_code=404, detail="Asset not found")
        if _is_foreshadowing_asset(existing_asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        _require_token_write_access(project, authorization)
        deleted_assets = [_save_asset_deletion_state(asset, is_deleted=True) for asset in _asset_lineage_members(project_id, existing_asset)]
        deleted = max(deleted_assets, key=lambda asset: (asset.version, asset.updated_at, asset.created_at)) if deleted_assets else _save_asset_deletion_state(existing_asset, is_deleted=True)
        _emit_asset_updated_event(project_id, asset_id, deleted.asset_type.value, "deleted", branch=deleted.branch)
        return {"message": f"Asset {asset_id} deleted", "asset_id": asset_id, "deleted_at": deleted.deleted_at.isoformat() if deleted.deleted_at else None}

    @app.post("/api/projects/{project_id}/assets/{asset_id}/restore", response_model=Asset)
    def restore_project_asset(project_id: str, asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> Asset:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        branch = _require_project_branch(project, branch)
        _require_token_write_access(project, authorization)
        existing_asset = state.store.get_asset_by_id(asset_id)
        if existing_asset is None or existing_asset.project_id != project_id or existing_asset.branch != branch:
            raise HTTPException(status_code=404, detail="Asset not found")
        if _is_foreshadowing_asset(existing_asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        restored_assets = [_save_asset_deletion_state(asset, is_deleted=False) for asset in _asset_lineage_members(project_id, existing_asset)]
        result = max(restored_assets, key=lambda asset: (asset.version, asset.updated_at, asset.created_at)) if restored_assets else _save_asset_deletion_state(existing_asset, is_deleted=False)
        _emit_asset_updated_event(project_id, asset_id, result.asset_type.value, "restored", branch=result.branch)
        return result

    @app.get("/api/projects/{project_id}/assets/{asset_type}/versions", response_model=list[Asset])
    def list_asset_versions(project_id: str, asset_type: AssetType, branch: str = "main", authorization: str | None = Header(default=None)) -> list[Asset]:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        return [asset for asset in state.store.list_asset_versions(project_id, asset_type, branch=branch) if not asset.is_deleted and not _is_spot_fix_candidate(asset)]

    @app.post("/api/projects/{project_id}/assets/{asset_type}/diff")
    def diff_asset_versions(project_id: str, asset_type: AssetType, branch: str = "main", authorization: str | None = Header(default=None)) -> Response:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        versions = [asset for asset in state.store.list_asset_versions(project_id, asset_type, branch=branch) if not asset.is_deleted and not _is_spot_fix_candidate(asset)]
        if not versions:
            raise HTTPException(status_code=404, detail="No versions found")
        versions_sorted = sorted(versions, key=lambda a: a.version)
        if len(versions_sorted) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 versions to diff")
        old = versions_sorted[-2]
        new = versions_sorted[-1]
        old_lines = old.content.splitlines(keepends=True)
        new_lines = new.content.splitlines(keepends=True)
        diff = []
        import difflib
        for line in difflib.unified_diff(old_lines, new_lines, fromfile=f"v{old.version}", tofile=f"v{new.version}", n=3):
            diff.append(line.rstrip("\n"))
        return Response(content="\n".join(diff), media_type="text/x-diff")

    def _get_commentable_asset(project: Project, project_id: str, asset_id: str, branch: str) -> Asset:
        branch = _require_project_branch(project, branch)
        asset = state.store.get_asset_by_id(asset_id)
        if asset is None or asset.project_id != project_id or asset.branch != branch or asset.is_deleted:
            raise HTTPException(status_code=404, detail="Asset not found")
        if _is_foreshadowing_asset(asset):
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    def _validate_comment_anchor(asset: Asset, body: AddCommentRequest) -> None:
        if body.anchor_type == "paragraph":
            paragraphs = str(asset.content or "").split("\n\n")
            if body.paragraph_index is None or body.paragraph_index >= len(paragraphs):
                raise HTTPException(status_code=400, detail="Comment paragraph anchor is outside asset content")
            paragraph_length = len(paragraphs[body.paragraph_index])
            if body.start_offset is not None and body.start_offset > paragraph_length:
                raise HTTPException(status_code=400, detail="Comment start offset is outside paragraph content")
            if body.end_offset is not None and body.end_offset > paragraph_length:
                raise HTTPException(status_code=400, detail="Comment end offset is outside paragraph content")
        if body.anchor_type == "text":
            content_length = len(str(asset.content or ""))
            if body.start_offset is None or body.end_offset is None or body.start_offset > content_length or body.end_offset > content_length:
                raise HTTPException(status_code=400, detail="Comment text anchor is outside asset content")

    def _save_asset_comments(asset: Asset, comments: list[dict]) -> Asset:
        commented_asset = Asset(
            asset_id=asset.asset_id,
            project_id=asset.project_id,
            asset_type=asset.asset_type,
            branch=asset.branch,
            content=asset.content,
            source=asset.source,
            structured_data=dict(asset.structured_data),
            comments=comments,
        )
        try:
            return state.store.save_asset(
                commented_asset,
                expected_version=asset.version,
                lineage_origin_id=str(asset.structured_data.get("origin_asset_id") or asset.asset_id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/assets/{asset_id}/comments")
    def list_asset_comments(
        project_id: str,
        asset_id: str,
        branch: str = "main",
        status: Literal["open", "resolved", "all"] = "all",
        authorization: str | None = Header(default=None),
    ) -> list[dict]:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        asset = _get_commentable_asset(project, project_id, asset_id, branch)
        comments = list(asset.comments or [])
        if status != "all":
            comments = [comment for comment in comments if comment.get("status", "open") == status]
        return comments

    @app.post("/api/projects/{project_id}/assets/{asset_id}/comments")
    def add_asset_comment(project_id: str, asset_id: str, body: AddCommentRequest = Body(), authorization: str | None = Header(default=None)) -> dict:
        from uuid import uuid4 as _uuid4
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        actor_id = _require_token_write_access(project, authorization)
        asset = _get_commentable_asset(project, project_id, asset_id, body.branch)
        _validate_comment_anchor(asset, body)
        now = utc_now().isoformat()
        comment = {
            "id": f"cmt_{_uuid4().hex[:8]}",
            "author": body.author,
            "content": body.content,
            "status": body.status,
            "anchor_type": body.anchor_type,
            "paragraph_index": body.paragraph_index,
            "start_offset": body.start_offset,
            "end_offset": body.end_offset,
            "actor_id": actor_id or body.actor_id,
            "actor_name": body.actor_name,
            "role": body.role,
            "created_at": now,
            "updated_at": now,
        }
        if body.status == "resolved":
            comment["resolved_at"] = now
        new_comments = (asset.comments or []) + [comment]
        _save_asset_comments(asset, new_comments)
        _emit_asset_updated_event(project_id, asset_id, asset.asset_type.value, "commented", branch=asset.branch)
        return comment

    @app.patch("/api/projects/{project_id}/assets/{asset_id}/comments/{comment_id}")
    def update_asset_comment(project_id: str, asset_id: str, comment_id: str, body: UpdateCommentRequest = Body(), authorization: str | None = Header(default=None)) -> dict:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        actor_id = _require_token_write_access(project, authorization)
        asset = _get_commentable_asset(project, project_id, asset_id, body.branch)
        comments = list(asset.comments or [])
        index = next((i for i, comment in enumerate(comments) if comment.get("id") == comment_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Comment not found")
        updated = dict(comments[index])
        if body.content is not None:
            updated["content"] = body.content
        if body.status is not None:
            updated["status"] = body.status
            if body.status == "resolved" and not updated.get("resolved_at"):
                updated["resolved_at"] = utc_now().isoformat()
            if body.status == "open":
                updated.pop("resolved_at", None)
        if actor_id or body.actor_id:
            updated["actor_id"] = actor_id or body.actor_id
        if body.actor_name is not None:
            updated["actor_name"] = body.actor_name
        if body.role is not None:
            updated["role"] = body.role
        updated["updated_at"] = utc_now().isoformat()
        comments[index] = updated
        _save_asset_comments(asset, comments)
        _emit_asset_updated_event(project_id, asset_id, asset.asset_type.value, "comment_updated", branch=asset.branch)
        return updated

    @app.get("/api/projects/{project_id}/branches")
    def list_project_branches(project_id: str, authorization: str | None = Header(default=None)) -> list[str]:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        return project.branches or ["main"]

    @app.post("/api/projects/{project_id}/branches")
    def create_project_branch(project_id: str, body: CreateBranchRequest = Body(), authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _validate_branch_name(body.branch)
        branches = project.branches or ["main"]
        if branch in branches:
            raise HTTPException(status_code=409, detail=f"Branch '{branch}' already exists")
        project.branches = branches + [branch]
        project.updated_at = utc_now()
        state.store.save_project(project)
        return {"branch": branch, "branches": project.branches}

    @app.post("/api/projects/{project_id}/branches/{source_branch}/fork")
    def fork_project_branch(project_id: str, source_branch: str, body: ForkBranchRequest = Body(), authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        source_branch = _require_project_branch(project, source_branch)
        target_branch = _validate_branch_name(body.target_branch)
        branches = project.branches or ["main"]
        if target_branch in branches:
            raise HTTPException(status_code=409, detail=f"Branch '{target_branch}' already exists")
        if body.start_chapter is not None and body.start_chapter <= 0:
            raise HTTPException(status_code=400, detail="start_chapter must be positive")
        if body.end_chapter is not None and body.end_chapter <= 0:
            raise HTTPException(status_code=400, detail="end_chapter must be positive")
        if body.start_chapter is not None and body.end_chapter is not None and body.start_chapter > body.end_chapter:
            raise HTTPException(status_code=400, detail="start_chapter must be less than or equal to end_chapter")
        copied_assets: list[Asset] = []
        for source in state.store.list_assets(project_id, branch=source_branch):
            if source.is_deleted or _is_foreshadowing_asset(source):
                continue
            chapter_number = _asset_chapter_number(source)
            if body.start_chapter is not None or body.end_chapter is not None:
                if chapter_number is None:
                    continue
                if body.start_chapter is not None and chapter_number < body.start_chapter:
                    continue
                if body.end_chapter is not None and chapter_number > body.end_chapter:
                    continue
            copied = Asset(
                project_id=project_id,
                asset_type=source.asset_type,
                branch=target_branch,
                content=source.content,
                source=source.source,
                structured_data={**source.structured_data, "forked_from_asset_id": source.asset_id, "forked_from_branch": source_branch},
                comments=[],
            )
            copied_assets.append(state.store.save_asset(copied))
        project.branches = branches + [target_branch]
        project.updated_at = utc_now()
        state.store.save_project(project)
        return {"branch": target_branch, "source_branch": source_branch, "copied_asset_ids": [asset.asset_id for asset in copied_assets], "copied_count": len(copied_assets), "branches": project.branches}

    @app.get("/api/projects/{project_id}/branches/compare")
    def compare_project_branches(project_id: str, left: str, right: str, authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        left = _require_project_branch(project, left)
        right = _require_project_branch(project, right)

        def latest_by_chapter(asset_type: AssetType, branch: str) -> dict[int, Asset]:
            result: dict[int, Asset] = {}
            for asset in state.store.list_assets(project_id, asset_type, branch=branch):
                if asset.is_deleted or _is_spot_fix_candidate(asset):
                    continue
                chapter_number = _asset_chapter_number(asset)
                if chapter_number is None:
                    continue
                existing = result.get(chapter_number)
                if existing is None or (asset.version, asset.updated_at, asset.created_at) > (existing.version, existing.updated_at, existing.created_at):
                    result[chapter_number] = asset
            return result

        def comparison_data(asset: Asset) -> dict:
            ignored_keys = {"forked_from_asset_id", "forked_from_branch"}
            return {key: value for key, value in asset.structured_data.items() if key not in ignored_keys}

        left_chapters = latest_by_chapter(AssetType.chapter, left)
        right_chapters = latest_by_chapter(AssetType.chapter, right)
        chapter_diffs = []
        for chapter_number in sorted(set(left_chapters) | set(right_chapters)):
            left_asset = left_chapters.get(chapter_number)
            right_asset = right_chapters.get(chapter_number)
            if left_asset is None:
                status = "added"
            elif right_asset is None:
                status = "removed"
            elif left_asset.content != right_asset.content or comparison_data(left_asset) != comparison_data(right_asset):
                status = "changed"
            else:
                status = "unchanged"
            chapter_diffs.append({
                "chapter_number": chapter_number,
                "left_asset_id": left_asset.asset_id if left_asset else None,
                "right_asset_id": right_asset.asset_id if right_asset else None,
                "left_title": str(left_asset.structured_data.get("title", "")) if left_asset else None,
                "right_title": str(right_asset.structured_data.get("title", "")) if right_asset else None,
                "status": status,
            })

        left_reviews = latest_by_chapter(AssetType.review_note, left)
        right_reviews = latest_by_chapter(AssetType.review_note, right)
        audit_diffs = []
        for chapter_number in sorted(set(left_reviews) | set(right_reviews)):
            left_review = left_reviews.get(chapter_number)
            right_review = right_reviews.get(chapter_number)
            audit_diffs.append({
                "chapter_number": chapter_number,
                "left_asset_id": left_review.asset_id if left_review else None,
                "right_asset_id": right_review.asset_id if right_review else None,
                "left_approved": left_review.structured_data.get("approved") if left_review else None,
                "right_approved": right_review.structured_data.get("approved") if right_review else None,
                "left_failed_checks": left_review.structured_data.get("failed_checks", []) if left_review else [],
                "right_failed_checks": right_review.structured_data.get("failed_checks", []) if right_review else [],
                "status": "changed" if (left_review is None or right_review is None or left_review.content != right_review.content or comparison_data(left_review) != comparison_data(right_review)) else "unchanged",
            })
        return {"project_id": project_id, "left": left, "right": right, "chapter_diffs": chapter_diffs, "audit_diffs": audit_diffs}

    @app.post("/api/projects/{project_id}/export")
    def export_project(project_id: str, body: ExportProjectRequest, authorization: str | None = Header(default=None)) -> Response:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        export_candidate, blocked_reason = _ready_export_candidate(project_id, branch)
        if export_candidate is None:
            raise HTTPException(status_code=409, detail=blocked_reason)
        final_refs = export_candidate.structured_data.get("final_chapter_refs", [])
        ordered_chapters: list[Asset] = []
        for ref in final_refs:
            if not isinstance(ref, str):
                continue
            asset = state.store.get_asset_by_id(ref)
            if asset is not None and asset.project_id == project_id and asset.branch == branch and asset.asset_type == AssetType.final_chapter and not asset.is_deleted:
                ordered_chapters.append(asset)
        if not ordered_chapters:
            raise HTTPException(status_code=409, detail="导出候选引用的定稿章节不存在")
        ordered_chapters.sort(key=lambda item: _coerce_chapter_number(item.structured_data.get("chapter_number")) or 0)
        media_type, extension, renderer = EXPORTERS[body.format]
        content = renderer(project, ordered_chapters)
        filename_base = _sanitize_download_filename(project.title or project.project_id)
        if body.format in {"qidian", "jinjiang"}:
            filename_base = f"{filename_base}_{body.format}"
        filename = f"{filename_base}.{extension}"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/projects/{project_id}/style-profile")
    def get_style_profile(project_id: str, branch: str = Query(default="main"), authorization: str | None = Header(default=None)) -> dict:
        from storyforge.execution.style import build_style_profile as _api_build_style_profile
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)
        return _api_build_style_profile(state.store, project_id, branch=branch)

    @app.put("/api/projects/{project_id}/style-profile")
    def update_style_profile(project_id: str, body: StyleProfileUpdateRequest, authorization: str | None = Header(default=None)) -> dict:
        from storyforge.execution.style import build_style_profile as _api_build_style_profile
        project = _project_or_404(project_id)
        actor_id = _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        data = body.model_dump()
        structured_data = {
            "kind": "style_profile",
            "voice": data.get("voice"),
            "strengths": data.get("strengths") or [],
            "avoid": data.get("avoid") or [],
            "sensory_keywords": data.get("sensory_keywords") or [],
            "locked_fields": data.get("locked_fields") or [],
        }
        asset = Asset(
            project_id=project_id,
            asset_type=AssetType.rules,
            branch=branch,
            source="human",
            content=json.dumps(structured_data, ensure_ascii=False),
            structured_data=structured_data | {"actor_id": actor_id},
        )
        saved = state.store.save_asset(asset)
        _emit_asset_updated_event(project_id, saved.asset_id, saved.asset_type.value, "updated_style_profile", branch=saved.branch)
        return _api_build_style_profile(state.store, project_id, branch=branch)

    def _validate_spot_fix_payload(project_id: str, payload: dict, branch: str) -> tuple[Asset, dict]:
        chapter_asset_id = payload.get("chapter_asset_id")
        chapter = state.store.get_asset_by_id(chapter_asset_id) if isinstance(chapter_asset_id, str) and chapter_asset_id else None
        if chapter is None or chapter.project_id != project_id or chapter.branch != branch or chapter.asset_type != AssetType.chapter or chapter.is_deleted:
            raise HTTPException(status_code=404, detail="Chapter not found")
        if _is_spot_fix_candidate(chapter):
            raise HTTPException(status_code=400, detail="Cannot spot-fix a spot-fix candidate")

        fix_instruction = payload.get("fix_instruction")
        if not isinstance(fix_instruction, str) or not fix_instruction.strip():
            raise HTTPException(status_code=400, detail="fix_instruction is required")

        paragraph_indices = payload.get("paragraph_indices")
        paragraphs = _split_paragraphs(chapter.content)
        invalid_indices = [
            idx
            for idx in (paragraph_indices if isinstance(paragraph_indices, list) else [])
            if type(idx) is not int or idx < 0 or idx >= len(paragraphs)
        ]
        if not isinstance(paragraph_indices, list) or not paragraph_indices or invalid_indices:
            raise HTTPException(status_code=400, detail="paragraph_indices must target existing paragraphs")

        normalized_payload = dict(payload)
        normalized_payload.update(
            {
                "chapter_asset_id": chapter.asset_id,
                "paragraph_indices": paragraph_indices,
                "fix_instruction": fix_instruction,
                "branch": chapter.branch,
            }
        )
        return chapter, normalized_payload

    @app.post("/api/projects/{project_id}/tasks", response_model=TaskRecord)
    def queue_task(project_id: str, body: QueueTaskRequest, authorization: str | None = Header(default=None)) -> TaskRecord:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        payload = dict(body.payload)
        if body.task_type == TaskType.spot_fix:
            _, payload = _validate_spot_fix_payload(project_id, payload, branch)
        if body.task_type == TaskType.chapter_generation and payload.get("rewrite"):
            original_asset_id = payload.get("original_asset_id")
            original = state.store.get_asset_by_id(str(original_asset_id)) if original_asset_id else None
            if original is None or original.project_id != project_id or original.branch != branch or original.asset_type != AssetType.chapter or original.is_deleted:
                raise HTTPException(status_code=404, detail="Original chapter not found")
        task = TaskRecord(
            project_id=project_id,
            task_type=body.task_type,
            branch=branch,
            payload=payload,
            effective_config_snapshot=body.config,
            status=TaskStatus.accepted,
            current_step="accepted",
        )
        state.workflow._accept_and_queue(task)
        return task

    @app.post("/api/projects/{project_id}/runs/first-loop", response_model=list[TaskRecord])
    def queue_first_loop(project_id: str, body: FirstLoopRequest, authorization: str | None = Header(default=None)) -> list[TaskRecord]:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        try:
            if body.auto_run:
                return state.application.queue_and_run_first_loop(project_id, chapter_number=body.chapter_number, branch=branch)
            return state.application.queue_first_loop(project_id, chapter_number=body.chapter_number, branch=branch)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _get_owned_spot_fix_candidate(project_id: str, candidate_asset_id: str, branch: str) -> Asset:
        if state.store.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        candidate = state.store.get_asset_by_id(candidate_asset_id)
        if candidate is None or candidate.project_id != project_id or candidate.branch != branch or candidate.is_deleted or not _is_spot_fix_candidate(candidate):
            raise HTTPException(status_code=404, detail="Spot-fix candidate not found")
        original_asset_id = candidate.structured_data.get("original_asset_id")
        original = state.store.get_asset_by_id(str(original_asset_id)) if original_asset_id else None
        if original is None or original.project_id != project_id or original.branch != branch or original.asset_type != AssetType.chapter or original.is_deleted:
            raise HTTPException(status_code=404, detail="Original chapter not found")
        return candidate

    @app.post("/api/projects/{project_id}/spot-fix", response_model=TaskRecord)
    def create_spot_fix(project_id: str, body: SpotFixRequest, authorization: str | None = Header(default=None)) -> TaskRecord:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        chapter_for_branch = state.store.get_asset_by_id(body.chapter_asset_id)
        requested_branch = body.branch or (chapter_for_branch.branch if chapter_for_branch is not None else "main")
        _require_project_branch(project, requested_branch)
        chapter, payload = _validate_spot_fix_payload(project_id, body.model_dump(exclude_none=True), requested_branch)
        task = TaskRecord(
            project_id=project_id,
            task_type=TaskType.spot_fix,
            branch=chapter.branch,
            payload=payload,
            status=TaskStatus.accepted,
            current_step="accepted",
            input_asset_refs=[chapter.asset_id],
        )
        state.workflow._accept_and_queue(task)
        return task

    @app.get("/api/projects/{project_id}/spot-fix/{candidate_asset_id}/diff")
    def diff_spot_fix_candidate(project_id: str, candidate_asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        candidate = _get_owned_spot_fix_candidate(project_id, candidate_asset_id, branch)
        original = state.store.get_asset_by_id(str(candidate.structured_data["original_asset_id"]))
        if original is None:
            raise HTTPException(status_code=404, detail="Original chapter not found")
        indices = [idx for idx in candidate.structured_data.get("spot_fix_paragraphs", []) if isinstance(idx, int)]
        original_paragraphs = _split_paragraphs(original.content)
        candidate_paragraphs = _split_paragraphs(candidate.content)
        paragraph_diffs = []
        for idx in indices:
            paragraph_diffs.append(
                {
                    "index": idx,
                    "original": original_paragraphs[idx] if idx < len(original_paragraphs) else "",
                    "candidate": candidate_paragraphs[idx] if idx < len(candidate_paragraphs) else "",
                }
            )
        import difflib
        unified_diff = "\n".join(
            line.rstrip("\n")
            for line in difflib.unified_diff(
                original.content.splitlines(keepends=True),
                candidate.content.splitlines(keepends=True),
                fromfile="original",
                tofile="candidate",
                n=3,
            )
        )
        return {
            "candidate_asset_id": candidate.asset_id,
            "original_asset_id": original.asset_id,
            "branch": branch,
            "paragraphs": paragraph_diffs,
            "unified_diff": unified_diff,
        }

    @app.post("/api/projects/{project_id}/spot-fix/{candidate_asset_id}/accept", response_model=Asset)
    def accept_spot_fix_candidate(project_id: str, candidate_asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> Asset:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        candidate = _get_owned_spot_fix_candidate(project_id, candidate_asset_id, branch)
        original = state.store.get_asset_by_id(str(candidate.structured_data["original_asset_id"]))
        if original is None:
            raise HTTPException(status_code=404, detail="Original chapter not found")
        lineage_origin_id = str(original.structured_data.get("origin_asset_id") or original.asset_id)
        structured_data = dict(candidate.structured_data)
        structured_data.pop("is_spot_fix", None)
        structured_data["accepted_spot_fix_candidate_id"] = candidate.asset_id
        structured_data["original_asset_id"] = original.asset_id
        structured_data["origin_asset_id"] = lineage_origin_id
        asset = Asset(
            project_id=project_id,
            asset_type=AssetType.chapter,
            branch=branch,
            content=candidate.content,
            source="human",
            structured_data=structured_data,
        )
        accepted = state.store.save_asset(asset, lineage_origin_id=lineage_origin_id)
        _emit_asset_updated_event(project_id, accepted.asset_id, accepted.asset_type.value, "accepted_spot_fix", branch=accepted.branch)
        return accepted

    @app.post("/api/projects/{project_id}/spot-fix/{candidate_asset_id}/reject")
    def reject_spot_fix_candidate(project_id: str, candidate_asset_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        candidate = _get_owned_spot_fix_candidate(project_id, candidate_asset_id, branch)
        candidate.structured_data["spot_fix_status"] = "rejected"
        candidate.structured_data["rejected_at"] = utc_now().isoformat()
        state.store.save_asset(candidate)
        _emit_asset_updated_event(project_id, candidate.asset_id, candidate.asset_type.value, "rejected_spot_fix", branch=candidate.branch)
        return {"status": "rejected", "candidate_asset_id": candidate.asset_id, "branch": branch}

    @app.post("/api/projects/{project_id}/runtime/enqueue", response_model=RuntimeResponse)
    def enqueue_project_runtime(project_id: str, authorization: str | None = Header(default=None)) -> RuntimeResponse:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        state.runtime.enqueue_project(project_id)
        return RuntimeResponse(queued=True, project_id=project_id, mode="background")

    @app.get("/api/runtime/claims", response_model=ClaimListResponse)
    def list_runtime_claims(
        worker_id: str | None = None,
        project_id: str | None = None,
        stale: bool | None = None,
        heartbeat_overdue: bool | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ) -> ClaimListResponse:
        actor_id = _require_runtime_scope_read_access(project_id, authorization)
        now = utc_now()
        claims = [
            _to_claim_status_response(claim, now=now)
            for claim in state.runtime.list_project_claims()
            if (project_id is None or claim.project_id == project_id)
            and (project_id is not None or _actor_can_read_project(_project_or_404(claim.project_id), actor_id))
        ]
        stale_count = sum(1 for claim in claims if claim.stale)
        filtered_claims = [
            claim
            for claim in claims
            if (worker_id is None or claim.worker_id == worker_id)
            and (project_id is None or claim.project_id == project_id)
            and (stale is None or claim.stale == stale)
            and (heartbeat_overdue is None or claim.heartbeat_overdue == heartbeat_overdue)
        ]
        paged_claims = filtered_claims[offset : offset + limit]
        return ClaimListResponse(
            claims=paged_claims,
            total=len(claims),
            filtered_total=len(filtered_claims),
            offset=offset,
            limit=limit,
            stale_count=stale_count,
        )

    @app.delete("/api/runtime/claims/stale", response_model=BulkClaimActionResponse)
    def release_stale_runtime_claims(authorization: str | None = Header(default=None)) -> BulkClaimActionResponse:
        _require_runtime_scope_write_access(None, authorization)
        released_project_ids = state.runtime.release_stale_project_claims()
        return BulkClaimActionResponse(
            released_project_ids=released_project_ids,
            released_count=len(released_project_ids),
        )

    @app.get("/api/runtime/severity-presets", response_model=SeverityPresetListResponse)
    def list_runtime_severity_presets() -> SeverityPresetListResponse:
        presets = [_to_severity_preset_discovery_response(preset) for preset in SEVERITY_PRESETS.values()]
        return SeverityPresetListResponse(
            presets=presets,
            total=len(SEVERITY_PRESETS),
        )

    @app.get("/api/runtime/audits", response_model=RuntimeAuditListResponse)
    def list_runtime_audits(
        project_id: str | None = None,
        action: str | None = None,
        actor_worker_id: str | None = None,
        forced: bool | None = None,
        stale: bool | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        window: str | None = None,
        severity_preset: str | None = None,
        severity_focus: str | None = None,
        worker_claim_health_limit: int | None = Query(default=None, ge=1, le=500),
        project_claim_health_limit: int | None = Query(default=None, ge=1, le=500),
        worker_severity_limit: int | None = Query(default=None, ge=1, le=500),
        project_severity_limit: int | None = Query(default=None, ge=1, le=500),
        joined_severity_limit: int | None = Query(default=None, ge=1, le=500),
        stale_claim_weight: int | None = Query(default=None, ge=0, le=1000),
        heartbeat_overdue_weight: int | None = Query(default=None, ge=0, le=1000),
        lease_loss_weight: int | None = Query(default=None, ge=0, le=1000),
        forced_release_weight: int | None = Query(default=None, ge=0, le=1000),
        stale_release_weight: int | None = Query(default=None, ge=0, le=1000),
        recent_audit_weight: int | None = Query(default=None, ge=0, le=1000),
        worker_summary_limit: int | None = Query(default=None, ge=1, le=500),
        project_summary_limit: int | None = Query(default=None, ge=1, le=500),
        action_summary_limit: int | None = Query(default=None, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ) -> RuntimeAuditListResponse:
        actor_id = _require_runtime_scope_read_access(project_id, authorization)
        resolved_preset = _resolve_severity_preset(severity_preset)
        resolved_severity_focus = _resolve_contribution_filter(severity_focus)
        if resolved_severity_focus is None and resolved_preset is not None:
            resolved_severity_focus = resolved_preset.severity_focus
        effective_window = resolved_preset.window if resolved_preset is not None else None
        if window is not None:
            effective_window = window

        if resolved_preset is not None:
            if worker_severity_limit is None:
                worker_severity_limit = resolved_preset.worker_severity_limit
            if project_severity_limit is None:
                project_severity_limit = resolved_preset.project_severity_limit
            if joined_severity_limit is None:
                joined_severity_limit = max(
                    resolved_preset.worker_severity_limit or 0,
                    resolved_preset.project_severity_limit or 0,
                )
                if joined_severity_limit == 0:
                    joined_severity_limit = None
            if worker_summary_limit is None:
                worker_summary_limit = resolved_preset.worker_summary_limit
            if project_summary_limit is None:
                project_summary_limit = resolved_preset.project_summary_limit
            if action_summary_limit is None:
                action_summary_limit = resolved_preset.action_summary_limit

        base_weights = resolved_preset.weights if resolved_preset is not None else SeverityWeightsResponse()
        severity_weights = SeverityWeightsResponse(
            stale_claim_weight=base_weights.stale_claim_weight if stale_claim_weight is None else stale_claim_weight,
            heartbeat_overdue_weight=(
                base_weights.heartbeat_overdue_weight if heartbeat_overdue_weight is None else heartbeat_overdue_weight
            ),
            lease_loss_weight=base_weights.lease_loss_weight if lease_loss_weight is None else lease_loss_weight,
            forced_release_weight=(
                base_weights.forced_release_weight if forced_release_weight is None else forced_release_weight
            ),
            stale_release_weight=(
                base_weights.stale_release_weight if stale_release_weight is None else stale_release_weight
            ),
            recent_audit_weight=base_weights.recent_audit_weight if recent_audit_weight is None else recent_audit_weight,
        )

        created_after_at = _parse_iso8601_datetime(created_after, field_name="created_after")
        created_before_at = _parse_iso8601_datetime(created_before, field_name="created_before")
        if effective_window is not None and (created_after_at is not None or created_before_at is not None):
            raise HTTPException(status_code=400, detail="window cannot be combined with created_after or created_before")
        if effective_window is not None:
            created_after_at, created_before_at = _resolve_relative_audit_window(effective_window, now=utc_now())
        if created_after_at is not None and created_before_at is not None and created_after_at > created_before_at:
            raise HTTPException(status_code=400, detail="created_after must be less than or equal to created_before")

        records = [
            record
            for record in state.runtime.list_runtime_audits(project_id)
            if project_id is not None or _actor_can_read_project(_project_or_404(record.project_id), actor_id)
        ]
        total = len(records)
        filtered_records = [
            RuntimeAuditResponse(
                audit_id=record.audit_id,
                action=record.action,
                project_id=record.project_id,
                actor_worker_id=record.actor_worker_id,
                claim_worker_id=record.claim_worker_id,
                forced=record.forced,
                stale=record.stale,
                message=record.message,
                created_at=record.created_at.isoformat(),
            )
            for record in records
            if (action is None or record.action == action)
            and (actor_worker_id is None or record.actor_worker_id == actor_worker_id)
            and (forced is None or record.forced == forced)
            and (stale is None or record.stale == stale)
            and (created_after_at is None or record.created_at >= created_after_at)
            and (created_before_at is None or record.created_at <= created_before_at)
        ]
        now = utc_now()
        claim_statuses = [
            _to_claim_status_response(claim, now=now)
            for claim in state.runtime.list_project_claims()
            if (project_id is not None or _actor_can_read_project(_project_or_404(claim.project_id), actor_id))
            and (project_id is None or claim.project_id == project_id)
        ]
        worker_severity_summary = _to_worker_severity_summary(
            claim_statuses,
            filtered_records,
            weights=severity_weights,
            limit=None,
        )
        project_severity_summary = _to_project_severity_summary(
            claim_statuses,
            filtered_records,
            weights=severity_weights,
            limit=None,
        )
        if resolved_severity_focus is not None:
            worker_severity_summary = WorkerSeveritySummaryResponse(
                workers=[
                    item
                    for item in worker_severity_summary.workers
                    if _contribution_focus_matches(item.severity_contributions, resolved_severity_focus)
                ],
                total_workers=sum(
                    1
                    for item in worker_severity_summary.workers
                    if _contribution_focus_matches(item.severity_contributions, resolved_severity_focus)
                ),
            )
            project_severity_summary = ProjectSeveritySummaryResponse(
                projects=[
                    item
                    for item in project_severity_summary.projects
                    if _contribution_focus_matches(item.severity_contributions, resolved_severity_focus)
                ],
                total_projects=sum(
                    1
                    for item in project_severity_summary.projects
                    if _contribution_focus_matches(item.severity_contributions, resolved_severity_focus)
                ),
            )
        if worker_severity_limit is not None:
            worker_severity_summary = WorkerSeveritySummaryResponse(
                workers=worker_severity_summary.workers[:worker_severity_limit],
                total_workers=worker_severity_summary.total_workers,
            )
        if project_severity_limit is not None:
            project_severity_summary = ProjectSeveritySummaryResponse(
                projects=project_severity_summary.projects[:project_severity_limit],
                total_projects=project_severity_summary.total_projects,
            )
        joined_severity_summary = _to_joined_severity_summary(
            worker_severity_summary,
            project_severity_summary,
            limit=joined_severity_limit,
        )
        worker_severity_focus_summary = _to_severity_focus_summary(
            [item.severity_contributions for item in worker_severity_summary.workers]
        )
        project_severity_focus_summary = _to_severity_focus_summary(
            [item.severity_contributions for item in project_severity_summary.projects]
        )
        joined_severity_focus_summary = _to_severity_focus_summary(
            [item.severity_contributions for item in joined_severity_summary.items]
        )
        paged_records = filtered_records[offset : offset + limit]
        return RuntimeAuditListResponse(
            records=paged_records,
            total=total,
            filtered_total=len(filtered_records),
            offset=offset,
            limit=limit,
            severity_preset=_to_severity_preset_response(resolved_preset),
            lease_loss_summary=_to_lease_loss_summary(filtered_records),
            claim_health_summary=_to_claim_health_summary(claim_statuses),
            worker_claim_health_summary=_to_worker_claim_health_summary(
                claim_statuses, limit=worker_claim_health_limit
            ),
            project_claim_health_summary=_to_project_claim_health_summary(
                claim_statuses, limit=project_claim_health_limit
            ),
            severity_weights=severity_weights,
            worker_severity_focus_summary=worker_severity_focus_summary,
            project_severity_focus_summary=project_severity_focus_summary,
            joined_severity_focus_summary=joined_severity_focus_summary,
            worker_severity_summary=worker_severity_summary,
            project_severity_summary=project_severity_summary,
            joined_severity_summary=joined_severity_summary,
            worker_summary=_to_worker_audit_summary(filtered_records, limit=worker_summary_limit),
            project_summary=_to_project_audit_summary(filtered_records, limit=project_summary_limit),
            action_summary=_to_action_audit_summary(filtered_records, limit=action_summary_limit),
        )

    @app.post("/api/projects/{project_id}/runtime/process-now", response_model=DrainResponse)
    def process_project_now(project_id: str, branch: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> DrainResponse:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, branch) if branch is not None else None
        processed_count = state.runtime.process_project_now(project_id, branch=branch)
        completed_ids = [task.task_id for task in state.store.list_tasks(project_id, branch=branch) if task.status == TaskStatus.completed]
        return DrainResponse(processed_task_ids=completed_ids, processed_count=processed_count)

    @app.get("/api/projects/{project_id}/runtime/claim", response_model=ClaimResponse)
    def get_project_runtime_claim(project_id: str, authorization: str | None = Header(default=None)) -> ClaimResponse:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        claim = state.runtime.get_project_claim(project_id)
        if claim is None:
            return ClaimResponse(claim=None)
        return ClaimResponse(claim=_to_claim_status_response(claim, now=utc_now()))

    @app.delete("/api/projects/{project_id}/runtime/claim", response_model=ClaimActionResponse)
    def release_project_runtime_claim(project_id: str, force: bool = False, authorization: str | None = Header(default=None)) -> ClaimActionResponse:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        released = state.runtime.release_project_claim(project_id, force=force)
        if not released and state.runtime.get_project_claim(project_id) is not None and not force:
            raise HTTPException(status_code=409, detail="Project claim is owned by another worker")
        return ClaimActionResponse(project_id=project_id, released=released, forced=force)

    @app.post("/api/projects/{project_id}/workers/process-next", response_model=TaskRecord | None)
    def process_next_task(project_id: str, branch: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> TaskRecord | None:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, branch) if branch is not None else None
        return state.application.process_next(project_id, branch=branch)

    @app.post("/api/projects/{project_id}/workers/drain", response_model=DrainResponse)
    def drain_project_queue(project_id: str, branch: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> DrainResponse:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, branch) if branch is not None else None
        result = state.application.drain(project_id, branch=branch)
        return DrainResponse(
            processed_task_ids=result.processed_task_ids,
            processed_count=result.processed_count,
        )

    @app.get("/api/projects/{project_id}/tasks", response_model=list[TaskRecord])
    def list_project_tasks(project_id: str, branch: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> list[TaskRecord]:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_branch_for_project(project_id, branch)
        return state.store.list_tasks(project_id, branch=branch)

    @app.get("/api/tasks/{task_id}", response_model=TaskRecord)
    def get_task(task_id: str, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_read_access(task, authorization)
        return task

    @app.get("/api/tasks/{task_id}/events", response_model=list[EventRecord])
    def list_task_events(task_id: str, authorization: str | None = Header(default=None)) -> list[EventRecord]:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_read_access(task, authorization)
        return state.store.list_events(task_id)

    @app.post("/api/tasks/{task_id}/retry", response_model=TaskRecord)
    def retry_task(task_id: str, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_write_access(task, authorization)
        try:
            return state.application.retry_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/cancel", response_model=TaskRecord)
    def cancel_task(task_id: str, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_write_access(task, authorization)
        try:
            return state.application.cancel_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/start", response_model=TaskRecord)
    def start_task(task_id: str, body: TaskActionRequest, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_write_access(task, authorization)
        state.state_machine.transition(task, TaskStatus.running, step=body.step, progress=body.progress)
        state.store.save_task(task)
        state.workflow.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.started,
                step=body.step,
                message="Task started",
                progress=body.progress,
            )
        )
        return task

    @app.post("/api/tasks/{task_id}/complete", response_model=TaskRecord)
    def complete_task(task_id: str, body: TaskActionRequest, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_write_access(task, authorization)
        state.state_machine.transition(task, TaskStatus.completed, step=body.step, progress=body.progress)
        state.store.save_task(task)
        state.workflow.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.completed,
                step=body.step,
                message="Task completed",
                progress=body.progress,
            )
        )
        return task

    @app.post("/api/tasks/{task_id}/fail", response_model=TaskRecord)
    def fail_task(task_id: str, body: TaskActionRequest, authorization: str | None = Header(default=None)) -> TaskRecord:
        task = state.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _require_task_write_access(task, authorization)
        if task.status != TaskStatus.running:
            raise HTTPException(status_code=409, detail="Task must be running before it can fail")
        state.state_machine.transition(task, TaskStatus.failed, step=body.step, progress=body.progress, error=body.error)
        state.store.save_task(task)
        state.workflow.record_event(
            EventRecord(
                project_id=task.project_id,
                task_id=task.task_id,
                event_type=EventType.failed,
                step=body.step,
                message=body.error or "Task failed",
                progress=body.progress,
                payload={"error": body.error} if body.error else {},
            )
        )
        return task

    @app.post("/api/projects/{project_id}/runs/next-chapter", response_model=list[TaskRecord])
    def queue_next_chapter(project_id: str, body: NextChapterRequest, authorization: str | None = Header(default=None)) -> list[TaskRecord]:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        tasks = state.application.queue_next_chapter(project_id, from_chapter=body.from_chapter, branch=branch)
        if body.auto_run:
            state.application.drain(project_id, branch=branch)
        return tasks

    @app.post("/api/projects/{project_id}/runs/chapter-loop", response_model=list[TaskRecord])
    def queue_chapter_loop(project_id: str, body: ChapterLoopRequest, authorization: str | None = Header(default=None)) -> list[TaskRecord]:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        branch = _require_project_branch(project, body.branch)
        tasks = state.application.queue_chapter_loop(project_id, target_chapter=body.target_chapter, branch=branch)
        if body.auto_run:
            state.application.drain(project_id, branch=branch)
        return tasks

    @app.get("/api/projects/{project_id}/chapters", response_model=list[ChapterInfo])
    def list_chapters(project_id: str, branch: str = "main", authorization: str | None = Header(default=None)) -> list[ChapterInfo]:
        project = _project_or_404(project_id)
        _require_token_read_access(project, authorization)
        branch = _require_project_branch(project, branch)

        final_assets = state.store.list_assets(project_id, AssetType.final_chapter, branch=branch)
        chapter_by_number: dict[int, ChapterInfo] = {}
        for asset in final_assets:
            if asset.is_deleted:
                continue
            ch_num = _coerce_chapter_number(asset.structured_data.get("chapter_number"))
            if ch_num is None:
                continue
            chapter_by_number[ch_num] = ChapterInfo(
                chapter_number=ch_num,
                title=asset.structured_data.get("title", ""),
                status="final",
                asset_id=asset.asset_id,
                review_approved=True,
            )

        draft_assets = state.store.list_assets(project_id, AssetType.chapter, branch=branch)
        for asset in draft_assets:
            if asset.is_deleted or _is_spot_fix_candidate(asset):
                continue
            ch_num = _coerce_chapter_number(asset.structured_data.get("chapter_number"))
            if ch_num is None or ch_num in chapter_by_number:
                continue
            chapter_by_number[ch_num] = ChapterInfo(
                chapter_number=ch_num,
                title=asset.structured_data.get("title", ""),
                status="draft",
                asset_id=asset.asset_id,
            )

        tasks = state.store.list_tasks(project_id, branch=branch)
        final_chapters = {
            int(t.payload.get("chapter_number", 0))
            for t in tasks
            if t.task_type == TaskType.final_save and t.status == TaskStatus.completed
        }
        for ch_num, info in chapter_by_number.items():
            if ch_num in final_chapters:
                info.status = "final"

        # Include pending chapters from queued tasks
        for t in tasks:
            if t.task_type == TaskType.chapter_generation and t.status in (TaskStatus.queued, TaskStatus.running, TaskStatus.waiting_retry):
                ch_num = int(t.payload.get("chapter_number", 0))
                if ch_num and ch_num not in chapter_by_number:
                    chapter_by_number[ch_num] = ChapterInfo(
                        chapter_number=ch_num,
                        status="pending" if t.status == TaskStatus.queued else "running",
                    )
                elif ch_num in chapter_by_number and chapter_by_number[ch_num].status == "pending" and t.status == TaskStatus.running:
                    chapter_by_number[ch_num].status = "running"

        return sorted(chapter_by_number.values(), key=lambda ch: ch.chapter_number)

    # --- Rule engine endpoints ---

    @app.get("/api/projects/{project_id}/rules", response_model=list[Rule])
    def list_rules(project_id: str, layer: str | None = None, authorization: str | None = Header(default=None)) -> list[Rule]:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_read_access(project, authorization)
        if layer == RuleLayer.custom.value or layer is None:
            custom_rules = state.store.get_rules(layer=RuleLayer.custom.value, project_id=project_id, include_disabled=True)
            if layer == RuleLayer.custom.value:
                return custom_rules
        else:
            custom_rules = []
        universal_rules = state.store.get_rules(layer=RuleLayer.universal.value) if layer in (None, RuleLayer.universal.value) else []
        genre_rules = state.store.get_rules(layer=RuleLayer.genre.value, genre=project.genre) if layer in (None, RuleLayer.genre.value) and project.genre else []
        return [*universal_rules, *genre_rules, *custom_rules]

    @app.post("/api/projects/{project_id}/rules", response_model=Rule)
    def create_rule(project_id: str, req: RuleCreateRequest = Body(), authorization: str | None = Header(default=None)) -> Rule:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        try:
            layer = RuleLayer(req.layer)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid layer: {req.layer}. Must be one of: universal, genre, custom")
        if layer != RuleLayer.custom:
            raise HTTPException(status_code=400, detail="Only custom rules can be created from a project")
        rule = Rule(
            layer=layer,
            genre=None,
            project_id=project_id,
            name=req.name,
            description=req.description,
            enabled=req.enabled,
        )
        return state.store.save_rule(rule)

    def _get_owned_custom_rule(project_id: str, rule_id: str) -> Rule:
        if state.store.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        rule = state.store.get_rule(rule_id)
        if rule is None or rule.layer != RuleLayer.custom or rule.project_id != project_id:
            raise HTTPException(status_code=404, detail="Rule not found")
        return rule

    @app.patch("/api/projects/{project_id}/rules/{rule_id}", response_model=Rule)
    def update_rule(project_id: str, rule_id: str, req: RuleUpdateRequest = Body(), authorization: str | None = Header(default=None)) -> Rule:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        rule = _get_owned_custom_rule(project_id, rule_id)
        if req.enabled is not None:
            rule.enabled = req.enabled
        if req.name is not None:
            rule.name = req.name
        if req.description is not None:
            rule.description = req.description
        return state.store.save_rule(rule)

    @app.delete("/api/projects/{project_id}/rules/{rule_id}")
    def delete_project_rule(project_id: str, rule_id: str, authorization: str | None = Header(default=None)) -> dict:
        project = _project_or_404(project_id)
        _require_token_write_access(project, authorization)
        _get_owned_custom_rule(project_id, rule_id)
        state.store.delete_rule(rule_id)
        return {"status": "deleted"}

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: str, authorization: str | None = Header(default=None)) -> dict:
        rule = state.store.get_rule(rule_id)
        if rule is None or rule.layer != RuleLayer.custom or not rule.project_id:
            raise HTTPException(status_code=404, detail="Rule not found")
        project = _project_or_404(rule.project_id)
        _require_token_write_access(project, authorization)
        state.store.delete_rule(rule_id)
        return {"status": "deleted"}

    @app.post("/api/projects/{project_id}/rules/seed")
    def seed_rules(project_id: str, authorization: str | None = Header(default=None)) -> list[Rule]:
        project = state.store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        _require_token_write_access(project, authorization)
        return state.store.seed_universal_rules()

    return app


app = create_app()
