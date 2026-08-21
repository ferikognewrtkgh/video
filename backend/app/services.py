from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional
from uuid import uuid4

from .business import GenerationStateMachine
from .domain import (
    ContinuityIssue,
    ContinuityReport,
    GenerationRecipe,
    GenerationRun,
    GenerationStatus,
    Project,
    QAEvaluateRequest,
    QAReport,
    RouteDecision,
    RouteRequest,
    ShotSpec,
    TaskLifecycleStatus,
    WorkflowStatus,
)


PRESERVE_FIELDS = {
    "identity": "appearance_version",
    "outfit": "appearance_version",
    "prop_state": "holding",
    "location": "location",
    "time": "time_of_day",
}


class ContinuityEngine:
    """Validate local transitions and project-wide graph integrity."""

    def check(self, shot: ShotSpec, previous: Optional[ShotSpec]) -> ContinuityReport:
        issues: list[ContinuityIssue] = []
        if previous:
            pairs = (
                ("location", previous.end_state.location, shot.start_state.location),
                ("holding", sorted(previous.end_state.holding), sorted(shot.start_state.holding)),
                ("time_of_day", previous.end_state.time_of_day, shot.start_state.time_of_day),
                ("appearance_version", previous.end_state.appearance_version, shot.start_state.appearance_version),
            )
            allowed = set(shot.allowed_changes)
            preserved = {PRESERVE_FIELDS.get(name, name) for name in shot.must_preserve}
            for field, before, after in pairs:
                if before == after or field in allowed:
                    continue
                severity = "error" if field in preserved or field in {"location", "appearance_version"} else "warning"
                issues.append(ContinuityIssue(
                    code=f"UNEXPLAINED_{field.upper()}_CHANGE", severity=severity, field=field,
                    message=f"{field} 从 {before} 无过渡变为 {after}",
                    previous_value=before, current_value=after,
                ))
        return ContinuityReport(shot_id=shot.id, passed=not any(i.severity == "error" for i in issues), issues=issues)

    def check_project(self, project: Project) -> list[ContinuityReport]:
        by_id = {shot.id: shot for shot in project.shots}
        reports: Dict[str, ContinuityReport] = {}
        scene_by_id = {scene.id: scene for scene in project.scenes}
        version_owner = {
            appearance.id: character.id
            for character in project.characters
            for appearance in character.appearances
        }
        for shot in project.shots:
            previous = by_id.get(shot.previous_shot_id) if shot.previous_shot_id else None
            report = self.check(shot, previous)
            reports[shot.id] = report
            if shot.previous_shot_id and not previous:
                self._add(report, "MISSING_PREVIOUS_SHOT", "error", "previous_shot_id", "前序镜头不存在")
            if previous and previous.order >= shot.order:
                self._add(report, "INVALID_PREVIOUS_ORDER", "error", "order", "前序镜头顺序必须早于当前镜头")
            scene = scene_by_id.get(shot.scene_id)
            if not scene:
                self._add(report, "UNKNOWN_SCENE", "error", "scene_id", "镜头引用的场景不存在")
            elif shot.start_state.location != scene.location or shot.end_state.location != scene.location:
                self._add(report, "SCENE_LOCATION_MISMATCH", "error", "location", "Shot 状态地点与 Scene 定义不一致")
            for version in shot.character_versions:
                if version not in version_owner:
                    self._add(report, "UNKNOWN_APPEARANCE_VERSION", "error", "character_versions", f"未知服装版本：{version}")
                elif shot.characters and version_owner[version] not in shot.characters:
                    self._add(report, "APPEARANCE_CHARACTER_MISMATCH", "error", "character_versions", f"服装版本 {version} 不属于镜头角色")
        self._check_cycles(project.shots, reports)
        return sorted(reports.values(), key=lambda item: by_id[item.shot_id].order)

    def _check_cycles(self, shots: list[ShotSpec], reports: Dict[str, ContinuityReport]) -> None:
        previous = {shot.id: shot.previous_shot_id for shot in shots}
        for shot in shots:
            seen: set[str] = set()
            current: Optional[str] = shot.id
            while current:
                if current in seen:
                    self._add(reports[shot.id], "DEPENDENCY_CYCLE", "error", "previous_shot_id", "镜头依赖形成循环")
                    break
                seen.add(current)
                current = previous.get(current)

    @staticmethod
    def _add(report: ContinuityReport, code: str, severity: str, field: str, message: str) -> None:
        report.issues.append(ContinuityIssue(code=code, severity=severity, field=field, message=message))
        if severity == "error":
            report.passed = False


class RenderRouter:
    def decide(self, request: RouteRequest) -> RouteDecision:
        available = set(request.available_providers)

        def ensure(provider: str) -> None:
            if available and provider not in available:
                raise ValueError(f"Provider {provider} is unavailable for this route")

        if request.shot_type in {"narration", "static_display"} or request.motion_level == 0:
            ensure("comfyui")
            return RouteDecision(route="2.5d_parallax", provider="comfyui", estimated_cost=0.08, explanation="静态镜头使用关键帧景深与运镜，稳定且成本最低")
        if request.shot_type == "dialogue_closeup" and request.characters <= 1 and request.motion_level <= 1:
            ensure("liveportrait")
            return RouteDecision(route="portrait_drive", provider="liveportrait", estimated_cost=0.18, explanation="单人对白使用肖像与口型驱动，优先保持身份")
        if request.shot_type in {"establishing", "weather", "transition"}:
            ensure("cloud-video")
            return RouteDecision(route="t2v", provider="cloud-video", estimated_cost=min(0.9, request.max_cost), explanation="空镜不涉及核心角色，可直接文本生成视频")
        if request.shot_type == "premium_action" or request.motion_level >= 3 or request.camera_complexity >= 3:
            ensure("cloud-video")
            candidates = 3 if request.quality_priority >= 0.85 else 2
            return RouteDecision(route="premium_i2v", provider="cloud-video", estimated_cost=min(request.max_cost, 1.35 * candidates), explanation="复杂动作使用高质量 I2V 并生成多个候选", candidate_count=candidates)
        ensure("cloud-video")
        return RouteDecision(route="i2v", provider="cloud-video", estimated_cost=min(0.72, request.max_cost), explanation="轻动作从锁定关键帧生成，兼顾身份与运动")


class PromptCompiler:
    def compile(self, shot: ShotSpec, decision: RouteDecision, project: Optional[Project] = None) -> GenerationRecipe:
        character_fragments: list[str] = []
        scene_fragment = ""
        if project:
            appearances = {a.id: a for c in project.characters for a in c.appearances}
            character_fragments = [appearances[v].prompt_fragment for v in shot.character_versions if v in appearances]
            scene = next((scene for scene in project.scenes if scene.id == shot.scene_id), None)
            scene_fragment = f"{scene.name}, {scene.style}, {scene.time_of_day}" if scene else ""
        prompt = ", ".join(filter(None, [
            project.style if project else "vertical cinematic manga", scene_fragment,
            *character_fragments, shot.title, shot.action,
            f"{shot.shot_size}, {shot.camera_motion}",
            f"emotion {shot.start_state.emotion} to {shot.end_state.emotion}",
            "consistent face, consistent outfit, clean line art",
        ]))
        # Runtime fields must never affect a recipe/idempotency hash. Otherwise changing
        # queued -> generating would submit the same media job and charge twice.
        source = {
            "shot": shot.model_dump(exclude={"status", "route", "qa_score", "thumbnail"}),
            "characters": character_fragments, "scene": scene_fragment, "template": "mangaflow-v2",
        }
        digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return GenerationRecipe(
            id=f"recipe_{shot.id}_{digest[:8]}", shot_id=shot.id, provider=decision.provider,
            model=decision.route, prompt=prompt,
            negative_prompt="identity drift, outfit change, prop loss, extra fingers, flicker, watermark, text",
            template_version="mangaflow-v2", parameters={"duration": shot.duration_sec, "aspect_ratio": "9:16", "seed": int(digest[:6], 16)},
            input_hash=digest, route=decision.route, estimated_cost=decision.estimated_cost,
        )


class MockProvider:
    """Deterministic in-process provider with idempotency and cancellation."""

    def __init__(self) -> None:
        self._tasks: Dict[str, GenerationRun] = {}
        self._states = GenerationStateMachine()

    def restore(self, runs: list[GenerationRun]) -> None:
        for run in runs:
            self._tasks[run.idempotency_key] = run

    def submit(self, shot: ShotSpec, recipe: GenerationRecipe, project_id: str = "") -> GenerationRun:
        key = hashlib.sha256(f"{project_id}:{shot.id}:{recipe.id}:mock:{recipe.model}".encode()).hexdigest()
        if key in self._tasks:
            return self._tasks[key]
        run = GenerationRun(
            id=f"run_{uuid4().hex[:10]}", shot_id=shot.id, provider="mock",
            provider_task_id=f"mock_{uuid4().hex[:12]}", idempotency_key=key,
            status=GenerationStatus.queued, recipe_id=recipe.id,
        )
        self._states.transition(run, TaskLifecycleStatus.queued, "Accepted by deterministic mock queue")
        self._tasks[key] = run
        return run

    def advance(self, run: GenerationRun) -> GenerationRun:
        if run.status == GenerationStatus.queued:
            run.status, run.progress = GenerationStatus.running, 28
            self._states.transition(run, TaskLifecycleStatus.running, "Mock worker started")
        elif run.status == GenerationStatus.running and run.progress < 90:
            run.progress += 31
        elif run.status == GenerationStatus.running:
            run.status, run.progress = GenerationStatus.completed, 100
            self._states.transition(run, TaskLifecycleStatus.succeeded, "Mock artifact completed")
            run.output_uri, run.elapsed_sec = f"mock://renders/{run.shot_id}.mp4", 12.4
        return run

    def cancel(self, run: GenerationRun) -> None:
        self._states.transition(run, TaskLifecycleStatus.cancelled, "Cancelled by workflow")


REPAIR_STRATEGIES = {
    "identity_drift": "更换关键帧并加强主参考图权重，减少同镜头角色数量",
    "outfit_mismatch": "固定 AppearanceVersion 后重新编译 Prompt",
    "flicker": "缩短镜头并降低动作幅度，必要时切换 2.5D 路线",
    "chaotic_motion": "拆分镜头，避免人物动作与复杂运镜并发",
    "bad_composition": "仅重生成关键帧，保留已通过的音频节点",
    "lip_sync": "仅重跑音频与口型节点，不重跑视频",
    "provider_timeout": "先查询原 Provider 任务状态，避免重复提交扣费",
    "black_frames": "拒绝当前产物并检查生成节点与编码链路",
    "duration_mismatch": "按 ShotSpec 时长重新裁剪或生成",
    "resolution_mismatch": "在交付节点统一缩放到目标分辨率",
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
    def command(self, project: Project, command: str) -> Project:
        transitions = {
            "start": {WorkflowStatus.ready, WorkflowStatus.paused, WorkflowStatus.failed},
            "pause": {WorkflowStatus.running},
            "resume": {WorkflowStatus.paused, WorkflowStatus.failed},
            "cancel": {WorkflowStatus.running, WorkflowStatus.paused},
        }
        if command not in transitions or project.workflow_status not in transitions[command]:
            raise ValueError(f"不能在 {project.workflow_status.value} 状态执行 {command}")
        project.workflow_status = {
            "start": WorkflowStatus.running, "pause": WorkflowStatus.paused,
            "resume": WorkflowStatus.running, "cancel": WorkflowStatus.cancelled,
        }[command]
        return project
