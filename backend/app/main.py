from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .demo import build_demo_project
from .domain import (
    CreateProjectRequest, QAEvaluateRequest, RouteRequest, WorkflowCommand,
    WorkflowStatus,
)
from .services import ContinuityEngine, MockProvider, PromptCompiler, QualityEngine, RenderRouter, WorkflowManager
from .store import store

app = FastAPI(title="MangaFlow Studio API", version="0.1.0", description="Continuity-first agentic manga production platform")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

continuity = ContinuityEngine()
router = RenderRouter()
compiler = PromptCompiler()
provider = MockProvider()
quality = QualityEngine()
workflow = WorkflowManager(
    Path(os.getenv("MANGAFLOW_CHECKPOINT_DIR", str(Path(tempfile.gettempdir()) / "mangaflow-checkpoints")))
)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "mangaflow-api", "provider": "mock"}


@app.get("/api/projects")
def list_projects():
    return [{"id": p.id, "name": p.name, "episode_title": p.episode_title, "status": p.workflow_status, "progress": round(100 * sum(s.status == "completed" for s in p.shots) / len(p.shots)), "cost": round(sum(c.amount for c in p.cost_events), 2)} for p in store.list()]


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    try:
        return store.get(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")


@app.post("/api/projects", status_code=201)
def create_project(request: CreateProjectRequest):
    project = build_demo_project().model_copy(deep=True)
    project.id = f"project_{uuid4().hex[:8]}"
    project.name = request.name
    project.source_text = request.source_text
    project.logline = request.source_text.strip().replace("\n", " ")[:96]
    project.style = request.style
    project.target_duration_sec = request.target_duration_sec
    project.workflow_status = WorkflowStatus.waiting_asset_approval
    project.workflow_step = 3
    return store.put(project)


def _shot_and_previous(project_id: str, shot_id: str):
    try:
        project = store.get(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    previous = next((s for s in project.shots if s.id == shot.previous_shot_id), None)
    return project, shot, previous


@app.get("/api/projects/{project_id}/continuity")
def check_all_continuity(project_id: str):
    try:
        project = store.get(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    reports = []
    for shot in project.shots:
        previous = next((s for s in project.shots if s.id == shot.previous_shot_id), None)
        reports.append(continuity.check(shot, previous))
    return {"passed": all(r.passed for r in reports), "reports": reports, "issue_count": sum(len(r.issues) for r in reports)}


@app.post("/api/projects/{project_id}/shots/{shot_id}/route")
def route_shot(project_id: str, shot_id: str, request: RouteRequest):
    _, shot, _ = _shot_and_previous(project_id, shot_id)
    decision = router.decide(request)
    shot.route = decision.route
    return decision


@app.post("/api/projects/{project_id}/shots/{shot_id}/generate", status_code=202)
def generate_shot(project_id: str, shot_id: str):
    project, shot, previous = _shot_and_previous(project_id, shot_id)
    report = continuity.check(shot, previous)
    if not report.passed:
        raise HTTPException(409, detail={"message": "Continuity gate failed", "report": report.model_dump()})
    decision = router.decide(RouteRequest(shot_type=shot.shot_type, characters=len(shot.characters), motion_level=3 if "premium" in shot.shot_type else 1, camera_complexity=2))
    recipe = compiler.compile(shot, decision)
    run = provider.submit(shot, recipe)
    if not any(r.idempotency_key == run.idempotency_key for r in project.generation_runs):
        project.generation_runs.append(run)
    shot.status = "generating"
    workflow.save(project)
    return {"run": run, "recipe": recipe, "route": decision}


@app.post("/api/projects/{project_id}/runs/{run_id}/tick")
def tick_run(project_id: str, run_id: str):
    try:
        project = store.get(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    run = next((r for r in project.generation_runs if r.id == run_id), None)
    if not run:
        raise HTTPException(404, "Generation run not found")
    provider.advance(run)
    shot = next(s for s in project.shots if s.id == run.shot_id)
    if run.status.value == "completed":
        shot.status = "completed"
        run.cost = 0.18
    workflow.save(project)
    return run


@app.post("/api/projects/{project_id}/shots/{shot_id}/qa")
def evaluate_shot(project_id: str, shot_id: str, request: QAEvaluateRequest):
    project, shot, _ = _shot_and_previous(project_id, shot_id)
    previous_attempts = [q for q in project.qa_reports if q.shot_id == shot_id]
    report = quality.evaluate(shot_id, request, len(previous_attempts))
    project.qa_reports.append(report)
    shot.qa_score = report.score
    shot.status = "needs_review" if report.needs_human_review else "repairing" if report.score < 75 else "completed"
    workflow.save(project)
    return report


@app.post("/api/projects/{project_id}/workflow")
def workflow_command(project_id: str, request: WorkflowCommand):
    try:
        project = store.get(project_id)
        return workflow.command(project, request.command)
    except KeyError:
        raise HTTPException(404, "Project not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/projects/{project_id}/approve-assets")
def approve_assets(project_id: str):
    try:
        project = store.get(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    for character in project.characters:
        character.locked = True
        for appearance in character.appearances:
            appearance.locked = True
    for scene in project.scenes:
        scene.locked = True
    project.workflow_status = WorkflowStatus.ready
    project.workflow_step = 4
    workflow.save(project)
    return {"approved": True, "locked_assets": sum(len(c.appearances) for c in project.characters) + len(project.scenes), "project": project}
