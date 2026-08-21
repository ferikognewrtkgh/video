from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4

from .domain import (
    ContinuityIssue,
    ContinuityReport,
    CostEvent,
    GenerationRecipe,
    GenerationRun,
    GenerationStatus,
    MetricScores,
    Project,
    QAReport,
    QAEvaluateRequest,
    RouteDecision,
    RouteRequest,
    ShotSpec,
    WorkflowStatus,
)


class ContinuityEngine:
    """Deterministic graph validation: structured state is the source of truth."""

    def check(self, shot: ShotSpec, previous: Optional[ShotSpec]) -> ContinuityReport:
        issues = []
        if previous:
            pairs = (
                ("location", previous.end_state.location, shot.start_state.location),
                ("holding", sorted(previous.end_state.holding), sorted(shot.start_state.holding)),
                ("time_of_day", previous.end_state.time_of_day, shot.start_state.time_of_day),
                ("appearance_version", previous.end_state.appearance_version, shot.start_state.appearance_version),
            )
            allowed = set(shot.allowed_changes)
            for field, before, after in pairs:
                if before != after and field not in allowed:
                    severity = "error" if field in {"location", "appearance_version"} else "warning"
                    issues.append(
                        ContinuityIssue(
                            code=f"UNEXPLAINED_{field.upper()}_CHANGE",
                            severity=severity,
                            field=field,
                            message=f"{field} 从 {before} 无过渡变为 {after}",
                            previous_value=before,
                            current_value=after,
                        )
                    )
        return ContinuityReport(shot_id=shot.id, passed=not any(i.severity == "error" for i in issues), issues=issues)


class RenderRouter:
    def decide(self, request: RouteRequest) -> RouteDecision:
        if request.shot_type in {"narration", "static_display"} or request.motion_level == 0:
            return RouteDecision(route="2.5d_parallax", provider="comfyui", estimated_cost=0.08, explanation="静态镜头优先使用关键帧景深与运镜，稳定且低成本")
        if request.shot_type == "dialogue_closeup" and request.characters <= 1 and request.motion_level <= 1:
            return RouteDecision(route="portrait_drive", provider="liveportrait", estimated_cost=0.18, explanation="单人对白使用肖像与口型驱动，优先保持身份")
        if request.shot_type in {"establishing", "weather", "transition"}:
            return RouteDecision(route="t2v", provider="cloud-video", estimated_cost=min(0.9, request.max_cost), explanation="空镜不涉及核心角色，可直接文本生成视频")
        if request.motion_level >= 3 or request.camera_complexity >= 3:
            candidates = 3 if request.quality_priority >= 0.85 else 2
            cost = min(request.max_cost, 1.35 * candidates)
            return RouteDecision(route="premium_i2v", provider="cloud-video", estimated_cost=cost, explanation="复杂动作使用高质量 I2V 并生成多个候选", candidate_count=candidates)
        return RouteDecision(route="i2v", provider="cloud-video", estimated_cost=min(0.72, request.max_cost), explanation="轻动作从锁定关键帧生成，兼顾身份与运动")


class PromptCompiler:
    def compile(self, shot: ShotSpec, decision: RouteDecision) -> GenerationRecipe:
        prompt = ", ".join(
            part
            for part in [
                "vertical cinematic manga",
                shot.title,
                shot.action,
                f"{shot.shot_size}, {shot.camera_motion}",
                f"emotion shifts from {shot.start_state.emotion} to {shot.end_state.emotion}",
                "consistent face, consistent outfit, clean line art",
            ]
            if part
        )
        digest = hashlib.sha256(json.dumps(shot.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return GenerationRecipe(
            id=f"recipe_{shot.id}_{digest[:8]}",
            shot_id=shot.id,
            provider=decision.provider,
            model="mock-cinematic-v1" if decision.provider == "mock" else decision.route,
            prompt=prompt,
            negative_prompt="identity drift, outfit change, extra fingers, flicker, watermark, text",
            parameters={"duration": shot.duration_sec, "aspect_ratio": "9:16", "seed": int(digest[:6], 16)},
            input_hash=digest,
            route=decision.route,
            estimated_cost=decision.estimated_cost,
        )


class MockProvider:
    """Idempotent provider used by development, demos and automated tests."""

    def __init__(self) -> None:
        self._tasks: Dict[str, GenerationRun] = {}

    def submit(self, shot: ShotSpec, recipe: GenerationRecipe) -> GenerationRun:
        key = hashlib.sha256(f"{shot.id}:{recipe.input_hash}:{recipe.model}".encode()).hexdigest()
        if key in self._tasks:
            return self._tasks[key]
        run = GenerationRun(
            id=f"run_{uuid4().hex[:10]}",
            shot_id=shot.id,
            provider="mock",
            provider_task_id=f"mock_{uuid4().hex[:12]}",
            idempotency_key=key,
            status=GenerationStatus.queued,
            recipe_id=recipe.id,
        )
        self._tasks[key] = run
        return run

    def advance(self, run: GenerationRun) -> GenerationRun:
        if run.status == GenerationStatus.queued:
            run.status, run.progress = GenerationStatus.running, 28
        elif run.status == GenerationStatus.running and run.progress < 90:
            run.progress += 31
        elif run.status == GenerationStatus.running:
            run.status, run.progress = GenerationStatus.completed, 100
            run.output_uri = f"mock://renders/{run.shot_id}.mp4"
            run.elapsed_sec = 12.4
        return run


REPAIR_STRATEGIES = {
    "identity_drift": "更换关键帧并加强主参考图权重，减少同镜头角色数量",
    "outfit_mismatch": "固定 AppearanceVersion 后重新编译 Prompt",
    "flicker": "缩短镜头并降低动作幅度，必要时切换 2.5D 路线",
    "chaotic_motion": "拆分镜头，避免人物动作与复杂运镜并发",
    "bad_composition": "仅重生成关键帧，保留已通过的音频节点",
    "lip_sync": "仅重跑音频与口型节点，不重跑视频",
    "provider_timeout": "先查询原 Provider 任务状态，避免重复提交扣费",
}


class QualityEngine:
    def evaluate(self, shot_id: str, request: QAEvaluateRequest, attempt: int = 0) -> QAReport:
        m = request.metrics
        score = round(100 * (0.30*m.identity + 0.20*m.prompt_alignment + 0.20*m.temporal_stability + 0.15*m.motion + 0.15*m.aesthetics), 1)
        repair = "；".join(REPAIR_STRATEGIES.get(f, f"人工复核：{f}") for f in request.failures) or None
        passed = request.hard_gate_passed and score >= 75
        return QAReport(
            id=f"qa_{uuid4().hex[:10]}", shot_id=shot_id, hard_gate_passed=request.hard_gate_passed,
            score=score, metrics=m, failures=request.failures, repair_strategy=repair,
            attempt=attempt, needs_human_review=not passed and attempt >= 2,
        )


class WorkflowManager:
    def __init__(self, checkpoint_dir: Path) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def command(self, project: Project, command: str) -> Project:
        transitions = {
            "start": {WorkflowStatus.ready, WorkflowStatus.paused, WorkflowStatus.failed},
            "pause": {WorkflowStatus.running},
            "resume": {WorkflowStatus.paused, WorkflowStatus.failed},
            "cancel": {WorkflowStatus.running, WorkflowStatus.paused},
        }
        if command not in transitions or project.workflow_status not in transitions[command]:
            raise ValueError(f"不能在 {project.workflow_status.value} 状态执行 {command}")
        target = {
            "start": WorkflowStatus.running, "pause": WorkflowStatus.paused,
            "resume": WorkflowStatus.running, "cancel": WorkflowStatus.cancelled,
        }[command]
        project.workflow_status = target
        self.save(project)
        return project

    def save(self, project: Project) -> None:
        path = self.checkpoint_dir / f"{project.id}.json"
        path.write_text(project.model_dump_json(indent=2), encoding="utf-8")


def clone_project(project: Project) -> Project:
    return deepcopy(project)
