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


class MetricScores(BaseModel):
    identity: float
    prompt_alignment: float
    temporal_stability: float
    motion: float
    aesthetics: float


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


class ArtifactDependency(BaseModel):
    id: str
    upstream_asset_id: str
    downstream_asset_id: str
    relation: str
    invalidated: bool = False


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
    updated_at: str = Field(default_factory=utc_now)


class CreateProjectRequest(BaseModel):
    name: str
    source_text: str = Field(min_length=20)
    style: str = "cinematic manga, warm noir"
    target_duration_sec: int = Field(default=52, ge=45, le=60)


class RouteRequest(BaseModel):
    shot_type: str
    characters: int = 1
    motion_level: int = Field(default=1, ge=0, le=3)
    camera_complexity: int = Field(default=1, ge=0, le=3)
    identity_priority: float = Field(default=0.9, ge=0, le=1)
    quality_priority: float = Field(default=0.8, ge=0, le=1)
    max_cost: float = Field(default=2, gt=0)


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
