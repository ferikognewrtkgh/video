from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStatus(str, Enum):
    draft = "draft"
    waiting_asset_approval = "waiting_asset_approval"
    ready = "ready"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class GenerationStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Role(str, Enum):
    owner = "owner"
    producer = "producer"
    creator = "creator"
    reviewer = "reviewer"
    viewer = "viewer"


class TaskLifecycleStatus(str, Enum):
    created = "created"
    queued = "queued"
    submitting = "submitting"
    submitted = "submitted"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"


class RetryClass(str, Enum):
    retryable = "retryable"
    non_retryable = "non_retryable"
    manual_review = "manual_review"


class ContinuityState(BaseModel):
    location: str
    emotion: str = "neutral"
    holding: List[str] = Field(default_factory=list)
    time_of_day: str = "day"
    appearance_version: Optional[str] = None


class ShotSpec(BaseModel):
    id: str
    episode_id: str = "ep01"
    scene_id: str
    order: int
    title: str
    duration_sec: float = Field(gt=0, le=20)
    shot_type: str
    shot_size: str
    camera_motion: str
    characters: List[str] = Field(default_factory=list)
    character_versions: List[str] = Field(default_factory=list)
    start_state: ContinuityState
    action: str
    end_state: ContinuityState
    dialogue: str = ""
    reference_asset_ids: List[str] = Field(default_factory=list)
    previous_shot_id: Optional[str] = None
    must_preserve: List[str] = Field(default_factory=list)
    allowed_changes: List[str] = Field(default_factory=list)
    status: str = "ready"
    route: Optional[str] = None
    qa_score: Optional[float] = None
    thumbnail: Optional[str] = None
    intent: str = ""
    lens: str = "standard"
    weather: str = ""
    lip_sync_required: bool = False
    first_frame_asset_id: Optional[str] = None
    last_frame_asset_id: Optional[str] = None
    quality_budget: float = Field(default=0.8, ge=0, le=1)
    deadline_at: Optional[str] = None
    assignee_id: Optional[str] = None
    stale_reasons: List[str] = Field(default_factory=list)


class CharacterAppearanceVersion(BaseModel):
    id: str
    character_id: str
    name: str
    outfit: str
    palette: List[str]
    locked: bool = False
    prompt_fragment: str


class Character(BaseModel):
    id: str
    name: str
    role: str
    identity: str
    voice: str
    locked: bool = False
    appearances: List[CharacterAppearanceVersion]
    accent_color: str = "#ff7058"


class Scene(BaseModel):
    id: str
    name: str
    location: str
    time_of_day: str
    style: str
    locked: bool = False


class Event(BaseModel):
    id: str
    title: str
    summary: str
    tension: int = Field(ge=0, le=100)
    character_ids: List[str]
    leads_to: List[str] = Field(default_factory=list)


class GenerationRecipe(BaseModel):
    id: str
    shot_id: str
    provider: str
    model: str
    prompt: str
    negative_prompt: str
    template_version: str = "mangaflow-v1"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    route: str
    estimated_cost: float


class TaskTransition(BaseModel):
    from_status: Optional[TaskLifecycleStatus] = None
    to_status: TaskLifecycleStatus
    reason: str = ""
    actor: str = "system"
    created_at: str = Field(default_factory=utc_now)


class GenerationRun(BaseModel):
    id: str
    shot_id: str
    provider: str
    provider_task_id: str
    idempotency_key: str
    status: GenerationStatus
    progress: int = 0
    retry_count: int = 0
    cost: float = 0
    elapsed_sec: float = 0
    output_uri: Optional[str] = None
    recipe_id: str
    created_at: str = Field(default_factory=utc_now)
    lifecycle_status: TaskLifecycleStatus = TaskLifecycleStatus.created
    timeline: List[TaskTransition] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_class: Optional[RetryClass] = None
    reconciled_at: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now)


class MetricScores(BaseModel):
    identity: float = Field(ge=0, le=1)
    prompt_alignment: float = Field(ge=0, le=1)
    temporal_stability: float = Field(ge=0, le=1)
    motion: float = Field(ge=0, le=1)
    aesthetics: float = Field(ge=0, le=1)


class QAReport(BaseModel):
    id: str
    shot_id: str
    hard_gate_passed: bool
    score: float
    metrics: MetricScores
    failures: List[str] = Field(default_factory=list)
    repair_strategy: Optional[str] = None
    attempt: int = 0
    needs_human_review: bool = False


class ContinuityIssue(BaseModel):
    code: str
    severity: str
    field: str
    message: str
    previous_value: Any = None
    current_value: Any = None


class ContinuityReport(BaseModel):
    shot_id: str
    passed: bool
    issues: List[ContinuityIssue]


class CostEvent(BaseModel):
    id: str
    shot_id: str
    category: str
    provider: str
    amount: float
    created_at: str = Field(default_factory=utc_now)


class Approval(BaseModel):
    id: str
    target_type: str
    target_id: str
    status: str
    reviewer: str = "Studio owner"
    created_at: str = Field(default_factory=utc_now)
    organization_id: str = "org_demo"
    comment: str = ""


class Episode(BaseModel):
    id: str
    project_id: str
    title: str
    order: int
    target_duration_sec: int
    status: WorkflowStatus = WorkflowStatus.draft


class Asset(BaseModel):
    id: str
    project_id: str
    asset_type: str
    name: str
    uri: Optional[str] = None
    content_hash: str
    version: int = 1
    locked: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    organization_id: str = "org_demo"
    logical_id: Optional[str] = None
    parent_version_id: Optional[str] = None
    source_uri: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    prompt: str = ""
    negative_prompt: str = ""
    lora_ids: List[str] = Field(default_factory=list)
    created_by: str = "system"
    created_at: str = Field(default_factory=utc_now)


class ArtifactDependency(BaseModel):
    id: str
    upstream_asset_id: str
    downstream_asset_id: str
    relation: str
    invalidated: bool = False
    invalidated_reason: Optional[str] = None
    invalidated_at: Optional[str] = None


class Organization(BaseModel):
    id: str
    name: str
    created_at: str = Field(default_factory=utc_now)


class User(BaseModel):
    id: str
    display_name: str
    active: bool = True


class Membership(BaseModel):
    organization_id: str
    user_id: str
    role: Role


class Principal(BaseModel):
    organization_id: str
    user_id: str
    role: Role


class ReviewComment(BaseModel):
    id: str
    organization_id: str
    project_id: str
    target_type: str
    target_id: str
    body: str
    author_id: str
    resolved: bool = False
    created_at: str = Field(default_factory=utc_now)


class AuditLog(BaseModel):
    id: str
    organization_id: str
    project_id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class DeliveryTrack(BaseModel):
    kind: str
    uri: Optional[str] = None
    checksum_sha256: Optional[str] = None
    file_size: Optional[int] = None
    ready: bool = False


class DeliveryManifest(BaseModel):
    id: str
    organization_id: str
    project_id: str
    status: str
    assembled: bool = False
    preset: Dict[str, Any]
    tracks: List[DeliveryTrack] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class Project(BaseModel):
    id: str
    name: str
    source_text: str
    logline: str
    style: str
    aspect_ratio: str = "9:16"
    target_duration_sec: int = 52
    workflow_status: WorkflowStatus = WorkflowStatus.waiting_asset_approval
    workflow_step: int = 3
    episode_title: str
    characters: List[Character]
    scenes: List[Scene]
    events: List[Event]
    shots: List[ShotSpec]
    generation_runs: List[GenerationRun] = Field(default_factory=list)
    qa_reports: List[QAReport] = Field(default_factory=list)
    cost_events: List[CostEvent] = Field(default_factory=list)
    approvals: List[Approval] = Field(default_factory=list)
    agent_trace: List[Dict[str, Any]] = Field(default_factory=list)
    data_mode: str = "generated"
    organization_id: str = "org_demo"
    owner_id: str = "user_owner"
    budget_limit: float = Field(default=8.0, gt=0)
    deadline_at: Optional[str] = None
    version: int = Field(default=1, ge=1)
    assets: List[Asset] = Field(default_factory=list)
    artifact_dependencies: List[ArtifactDependency] = Field(default_factory=list)
    comments: List[ReviewComment] = Field(default_factory=list)
    audit_logs: List[AuditLog] = Field(default_factory=list)
    delivery_manifests: List[DeliveryManifest] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class CreateProjectRequest(BaseModel):
    name: str
    source_text: str = Field(min_length=20)
    style: str = "cinematic manga, warm noir"
    target_duration_sec: int = Field(default=52, ge=45, le=60)
    budget_limit: float = Field(default=8.0, gt=0, le=100000)
    deadline_at: Optional[str] = None


class RouteRequest(BaseModel):
    shot_type: str
    characters: int = 1
    motion_level: int = Field(default=1, ge=0, le=3)
    camera_complexity: int = Field(default=1, ge=0, le=3)
    identity_priority: float = Field(default=0.9, ge=0, le=1)
    quality_priority: float = Field(default=0.8, ge=0, le=1)
    max_cost: float = Field(default=2, gt=0)
    available_providers: List[str] = Field(default_factory=list)
    deadline_seconds: Optional[int] = Field(default=None, gt=0)


class RouteDecision(BaseModel):
    route: str
    provider: str
    estimated_cost: float
    explanation: str
    candidate_count: int = 1


class WorkflowCommand(BaseModel):
    command: str


class QAEvaluateRequest(BaseModel):
    hard_gate_passed: bool = True
    metrics: MetricScores
    failures: List[str] = Field(default_factory=list)


class MediaInspectRequest(BaseModel):
    media_path: str
    expected_duration_sec: float = Field(gt=0, le=120)
    expected_width: Optional[int] = Field(default=None, gt=0)
    expected_height: Optional[int] = Field(default=None, gt=0)
    reference_image_path: Optional[str] = None


class MediaInspection(BaseModel):
    decodable: bool
    width: int = 0
    height: int = 0
    fps: float = 0
    duration_sec: float = 0
    frame_count: int = 0
    black_frame_ratio: float = 0
    flicker_score: float = 0
    reference_similarity: Optional[float] = None
    hard_gate_passed: bool
    failures: List[str] = Field(default_factory=list)
    metric_sources: Dict[str, str] = Field(default_factory=dict)


class AdaptationResult(BaseModel):
    logline: str
    episode_title: str
    characters: List[Character]
    scenes: List[Scene]
    events: List[Event]
    shots: List[ShotSpec]
    trace: List[Dict[str, Any]]


class CommentRequest(BaseModel):
    target_type: str
    target_id: str
    body: str = Field(min_length=1, max_length=2000)


class AssetVersionRequest(BaseModel):
    name: str
    uri: Optional[str] = None
    content_hash: str = Field(min_length=8)
    prompt: str = ""
    negative_prompt: str = ""
    source_uri: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DependencyRequest(BaseModel):
    upstream_asset_id: str
    downstream_asset_id: str
    relation: str = "references"


class StoryboardImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)
    expected_project_version: int = Field(ge=1)


class SourceDocumentRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_text: Optional[str] = None
    content_base64: Optional[str] = None
    max_segment_chars: int = Field(default=4000, ge=500, le=20000)
    overlap_chars: int = Field(default=200, ge=0, le=2000)


class ImportProjectRequest(SourceDocumentRequest):
    name: str
    style: str = "cinematic manga, warm noir"
    target_duration_sec: int = Field(default=52, ge=45, le=60)
    budget_limit: float = Field(default=8.0, gt=0, le=100000)
    deadline_at: Optional[str] = None


class SourceSegment(BaseModel):
    index: int
    text: str
    start_char: int
    end_char: int
    checksum_sha256: str


class SourceDocument(BaseModel):
    filename: str
    format: str
    text: str
    segments: List[SourceSegment]
    checksum_sha256: str
