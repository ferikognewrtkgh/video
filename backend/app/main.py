from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .adaptation import AdaptationAgent
from .business import (
    AuthenticationError,
    AuthorizationError,
    DeliveryService,
    DependencyService,
    GenerationStateMachine,
    IdentityDirectory,
    StoryboardExchange,
    add_audit,
    operational_metrics,
)
from .domain import (
    Approval,
    Asset,
    AssetVersionRequest,
    CommentRequest,
    CostEvent,
    CreateProjectRequest,
    DependencyRequest,
    GenerationRun,
    GenerationStatus,
    ImportProjectRequest,
    MediaInspectRequest,
    Principal,
    Project,
    QAEvaluateRequest,
    ReviewComment,
    RouteRequest,
    SourceDocumentRequest,
    StoryboardImportRequest,
    TaskLifecycleStatus,
    WorkflowCommand,
    WorkflowStatus,
    utc_now,
)
from .media_quality import MediaQualityAnalyzer
from .ingestion import DocumentIngestionError, DocumentIngestor
from .providers import MediaProvider, MediaRequest, ProviderState, create_media_provider
from .services import ContinuityEngine, MockProvider, PromptCompiler, QualityEngine, RenderRouter, WorkflowManager
from .store import ProjectStore


def create_app(
    checkpoint_dir: Path | None = None,
    media_root: Path | None = None,
    seed_demo: bool = True,
    environment: dict[str, str] | None = None,
    adaptation_agent: AdaptationAgent | None = None,
    identity_directory: IdentityDirectory | None = None,
    provider_override: tuple[str, MediaProvider | None] | None = None,
) -> FastAPI:
    env = environment or dict(os.environ)
    checkpoint_dir = checkpoint_dir or Path(env.get("MANGAFLOW_CHECKPOINT_DIR", str(Path(tempfile.gettempdir()) / "mangaflow-checkpoints")))
    media_root = (media_root or Path(env.get("MANGAFLOW_MEDIA_ROOT", str(checkpoint_dir.parent)))).resolve()
    store = ProjectStore(checkpoint_dir, seed_demo=seed_demo)
    continuity = ContinuityEngine()
    render_router = RenderRouter()
    compiler = PromptCompiler()
    mock_provider = MockProvider()
    quality = QualityEngine()
    media_quality = MediaQualityAnalyzer()
    workflow = WorkflowManager()
    task_states = GenerationStateMachine()
    dependencies = DependencyService()
    storyboard_exchange = StoryboardExchange()
    delivery_service = DeliveryService()
    document_ingestor = DocumentIngestor()
    identities = identity_directory or IdentityDirectory()
    agent = adaptation_agent or AdaptationAgent()
    comfy_workflow = _load_comfy_workflow(env.get("COMFYUI_WORKFLOW_PATH"))
    provider_name, remote_provider = provider_override or create_media_provider(env, comfy_workflow)
    for project in store.restore_runs():
        mock_provider.restore(project.generation_runs)

    app = FastAPI(title="MangaFlow Studio API", version="0.3.0", description="Continuity-first multi-tenant manga production platform")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.state.store = store
    app.state.provider_name = provider_name
    app.state.media_root = media_root
    app.state.identities = identities
    auth_mode = env.get("MANGAFLOW_AUTH_MODE", "dev")
    identity_secret = env.get("MANGAFLOW_IDENTITY_HMAC_SECRET", "")
    if auth_mode == "strict" and not identity_secret:
        raise ValueError("MANGAFLOW_IDENTITY_HMAC_SECRET is required in strict auth mode")

    def current_principal(
        organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
        user_id: str | None = Header(default=None, alias="X-User-ID"),
        signature: str | None = Header(default=None, alias="X-Identity-Signature"),
    ) -> Principal:
        if not organization_id and not user_id and auth_mode == "dev":
            organization_id, user_id = "org_demo", "user_owner"
        if not organization_id or not user_id:
            raise HTTPException(401, "X-Organization-ID and X-User-ID are required")
        if auth_mode == "strict":
            expected = hmac.new(identity_secret.encode(), f"{organization_id}:{user_id}".encode(), hashlib.sha256).hexdigest()
            if not signature or not hmac.compare_digest(expected, signature.removeprefix("sha256=")):
                raise HTTPException(401, "Identity signature is missing or invalid")
        try:
            return identities.authenticate(organization_id, user_id)
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc

    def authorize(principal: Principal, permission: str) -> None:
        try:
            identities.require(principal, permission)
        except AuthorizationError as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.get("/api/health")
    def health():
        return {
            "status": "ok", "service": "mangaflow-api", "provider": provider_name,
            "recovered_projects": len(list(store.restore_runs())), "version": app.version,
        }

    @app.get("/api/projects")
    def list_projects(principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        return [{
            "id": p.id, "name": p.name, "episode_title": p.episode_title,
            "status": p.workflow_status, "progress": round(100 * sum(s.status == "completed" for s in p.shots) / len(p.shots)),
            "cost": round(sum(c.amount for c in p.cost_events), 2), "data_mode": p.data_mode,
        } for p in store.list(principal.organization_id)]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        return _project_or_404(store, project_id, principal.organization_id)

    @app.post("/api/projects", status_code=201)
    def create_project(request: CreateProjectRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        return build_project(request, principal)

    def build_project(request: CreateProjectRequest, principal: Principal, import_metadata: dict | None = None):
        try:
            result = agent.run(request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        project = Project(
            id=f"project_{uuid4().hex[:8]}", name=request.name, source_text=request.source_text,
            logline=result.logline, style=request.style, target_duration_sec=request.target_duration_sec,
            workflow_status=WorkflowStatus.waiting_asset_approval, workflow_step=3,
            episode_title=result.episode_title, characters=result.characters, scenes=result.scenes,
            events=result.events, shots=result.shots, agent_trace=result.trace, data_mode="generated",
            organization_id=principal.organization_id, owner_id=principal.user_id,
            budget_limit=request.budget_limit, deadline_at=request.deadline_at,
        )
        reports = continuity.check_project(project)
        if any(not report.passed for report in reports):
            raise HTTPException(422, detail={"message": "Agent produced an invalid continuity graph", "reports": [r.model_dump() for r in reports]})
        if import_metadata:
            project.agent_trace.insert(0, {"node": "document_ingestion", "status": "completed", **import_metadata})
        add_audit(project, principal, "project.created", "project", project.id, source_length=len(request.source_text))
        return store.put(project)

    @app.post("/api/imports/inspect")
    def inspect_source_document(request: SourceDocumentRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        try:
            document = document_ingestor.ingest(request)
        except DocumentIngestionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "filename": document.filename,
            "format": document.format,
            "character_count": len(document.text),
            "checksum_sha256": document.checksum_sha256,
            "segments": document.segments,
            "preview": document.text[:500],
        }

    @app.post("/api/imports/projects", status_code=201)
    def create_project_from_document(request: ImportProjectRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        try:
            document = document_ingestor.ingest(request)
        except DocumentIngestionError as exc:
            raise HTTPException(422, str(exc)) from exc
        project_request = CreateProjectRequest(
            name=request.name,
            source_text=document.text,
            style=request.style,
            target_duration_sec=request.target_duration_sec,
            budget_limit=request.budget_limit,
            deadline_at=request.deadline_at,
        )
        return build_project(project_request, principal, {
            "filename": document.filename,
            "format": document.format,
            "segment_count": len(document.segments),
            "checksum_sha256": document.checksum_sha256,
        })

    def shot_and_previous(project_id: str, shot_id: str, principal: Principal):
        project = _project_or_404(store, project_id, principal.organization_id)
        shot = next((item for item in project.shots if item.id == shot_id), None)
        if not shot:
            raise HTTPException(404, "Shot not found")
        previous = next((item for item in project.shots if item.id == shot.previous_shot_id), None)
        return project, shot, previous

    @app.get("/api/projects/{project_id}/continuity")
    def check_all_continuity(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        project = _project_or_404(store, project_id, principal.organization_id)
        reports = continuity.check_project(project)
        return {"passed": all(r.passed for r in reports), "reports": reports, "issue_count": sum(len(r.issues) for r in reports)}

    @app.post("/api/projects/{project_id}/shots/{shot_id}/route")
    def route_shot(project_id: str, shot_id: str, request: RouteRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        project, shot, _ = shot_and_previous(project_id, shot_id, principal)
        try:
            decision = render_router.decide(request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        shot.route = decision.route
        add_audit(project, principal, "shot.routed", "shot", shot.id, route=decision.route, provider=decision.provider)
        store.save(project)
        return decision

    @app.post("/api/projects/{project_id}/shots/{shot_id}/generate", status_code=202)
    async def generate_shot(project_id: str, shot_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "generate")
        project, shot, previous = shot_and_previous(project_id, shot_id, principal)
        if project.workflow_status not in {WorkflowStatus.ready, WorkflowStatus.running}:
            raise HTTPException(409, f"Generation is blocked while workflow is {project.workflow_status.value}")
        report = continuity.check(shot, previous)
        if not report.passed:
            raise HTTPException(409, detail={"message": "Continuity gate failed", "report": report.model_dump()})
        decision = render_router.decide(RouteRequest(
            shot_type=shot.shot_type, characters=len(shot.characters),
            motion_level=3 if "premium" in shot.shot_type else 1, camera_complexity=2,
        ))
        recipe = compiler.compile(shot, decision, project)
        existing = next((run for run in project.generation_runs if run.recipe_id == recipe.id and run.status != GenerationStatus.failed), None)
        if existing:
            return {"run": existing, "recipe": recipe, "route": decision, "deduplicated": True}
        current_cost = sum(item.amount for item in project.cost_events)
        if current_cost + recipe.estimated_cost > project.budget_limit:
            raise HTTPException(402, detail={
                "message": "Project cost cap would be exceeded",
                "budget_limit": project.budget_limit,
                "current_cost": current_cost,
                "estimated_cost": recipe.estimated_cost,
            })
        if remote_provider:
            idempotency_key = hashlib.sha256(
                f"{project.id}:{shot.id}:{recipe.id}:{provider_name}:{recipe.model}".encode()
            ).hexdigest()
            run = GenerationRun(
                id=f"run_{uuid4().hex[:10]}", shot_id=shot.id, provider=provider_name,
                provider_task_id=f"pending:{idempotency_key}", idempotency_key=idempotency_key,
                status=GenerationStatus.queued, recipe_id=recipe.id,
            )
            task_states.transition(run, TaskLifecycleStatus.submitting, "Submitting to provider")
            try:
                task = await remote_provider.submit(MediaRequest(
                    prompt=recipe.prompt, model=recipe.model, idempotency_key=idempotency_key,
                    parameters=recipe.parameters, reference_uri=shot.reference_asset_ids[0] if shot.reference_asset_ids else None,
                ))
            except Exception as exc:
                normalized = remote_provider.normalize_error(exc)
                if normalized.outcome_unknown:
                    task_states.mark_unknown(run, exc)
                    project.generation_runs.append(run)
                    shot.status = "reconciling"
                    add_audit(project, principal, "generation.unknown", "run", run.id, error_code=normalized.code)
                    store.save(project)
                    return {"run": run, "recipe": recipe, "route": decision, "deduplicated": False, "reconciliation_required": True}
                task_states.transition(run, TaskLifecycleStatus.failed, normalized.message)
                run.error_code, run.error_message = normalized.code, normalized.message
                project.generation_runs.append(run)
                add_audit(project, principal, "generation.failed", "run", run.id, error_code=normalized.code)
                store.save(project)
                raise HTTPException(502, f"Provider submission failed: {normalized.message}") from exc
            run.provider_task_id = task.id
            task_states.transition(run, TaskLifecycleStatus.submitted, "Provider returned task id")
            task_states.transition(run, _task_lifecycle(task.status), f"Provider state is {task.status.value}")
        else:
            run = mock_provider.submit(shot, recipe, project.id)
        project.generation_runs.append(run)
        shot.status = "generating"
        add_audit(project, principal, "generation.submitted", "run", run.id, provider=provider_name, recipe_id=recipe.id)
        store.save(project)
        return {"run": run, "recipe": recipe, "route": decision, "deduplicated": False}

    @app.post("/api/projects/{project_id}/runs/{run_id}/tick")
    async def tick_run(project_id: str, run_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "generate")
        project = _project_or_404(store, project_id, principal.organization_id)
        if project.workflow_status in {WorkflowStatus.paused, WorkflowStatus.cancelled}:
            raise HTTPException(409, f"Worker cannot advance while workflow is {project.workflow_status.value}")
        run = next((item for item in project.generation_runs if item.id == run_id), None)
        if not run:
            raise HTTPException(404, "Generation run not found")
        if remote_provider:
            try:
                task = await remote_provider.query(run.provider_task_id)
            except Exception as exc:
                run.retry_count += 1
                normalized = remote_provider.normalize_error(exc)
                if normalized.outcome_unknown and run.lifecycle_status in {TaskLifecycleStatus.submitted, TaskLifecycleStatus.running}:
                    task_states.mark_unknown(run, exc)
                store.save(project)
                raise HTTPException(502, f"Provider query failed: {exc}") from exc
            target = _task_lifecycle(task.status)
            if target != run.lifecycle_status:
                task_states.transition(run, target, "Provider status poll")
            if task.status == ProviderState.succeeded:
                result = await remote_provider.fetch_result(run.provider_task_id)
                run.output_uri, run.cost, run.elapsed_sec = result.uri, result.cost, result.elapsed_sec
        else:
            mock_provider.advance(run)
        shot = next(item for item in project.shots if item.id == run.shot_id)
        if run.status == GenerationStatus.completed:
            shot.status = "completed"
            if not any(cost.shot_id == shot.id and cost.category == "video" for cost in project.cost_events):
                run.cost = 0.18 if provider_name == "mock" else run.cost
                project.cost_events.append(CostEvent(id=f"cost_{uuid4().hex[:8]}", shot_id=shot.id, category="video", provider=provider_name, amount=run.cost))
                add_audit(project, principal, "generation.succeeded", "run", run.id, cost=run.cost)
        store.save(project)
        return run

    @app.post("/api/projects/{project_id}/runs/{run_id}/reconcile")
    async def reconcile_run(project_id: str, run_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "generate")
        project = _project_or_404(store, project_id, principal.organization_id)
        run = next((item for item in project.generation_runs if item.id == run_id), None)
        if not run:
            raise HTTPException(404, "Generation run not found")
        if run.lifecycle_status != TaskLifecycleStatus.unknown:
            return {"run": run, "reconciled": False, "reason": "task is not UNKNOWN"}
        if remote_provider is None:
            raise HTTPException(409, "Mock tasks never require provider reconciliation")
        try:
            task = await remote_provider.recover(run.idempotency_key)
        except Exception as exc:
            run.retry_count += 1
            store.save(project)
            raise HTTPException(502, f"Provider reconciliation failed: {exc}") from exc
        if task is None:
            return {"run": run, "reconciled": False, "reason": "provider has no task for idempotency key"}
        run.provider_task_id = task.id
        task_states.transition(run, _task_lifecycle(task.status), "Recovered by idempotency lookup")
        run.reconciled_at = utc_now()
        run.error_code = run.error_message = None
        add_audit(project, principal, "generation.reconciled", "run", run.id, provider_task_id=task.id)
        store.save(project)
        return {"run": run, "reconciled": True}

    @app.post("/api/projects/{project_id}/shots/{shot_id}/qa")
    def evaluate_shot(project_id: str, shot_id: str, request: QAEvaluateRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project, shot, _ = shot_and_previous(project_id, shot_id, principal)
        attempts = [report for report in project.qa_reports if report.shot_id == shot_id]
        report = quality.evaluate(shot_id, request, len(attempts))
        project.qa_reports.append(report)
        shot.qa_score = report.score
        shot.status = "needs_review" if report.needs_human_review else "repairing" if report.score < 75 else "completed"
        add_audit(project, principal, "qa.evaluated", "shot", shot.id, score=report.score, hard_gate=report.hard_gate_passed)
        store.save(project)
        return {"report": report, "metric_source": "submitted/manual-review"}

    @app.post("/api/projects/{project_id}/shots/{shot_id}/inspect-media")
    def inspect_media(project_id: str, shot_id: str, request: MediaInspectRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project, shot, _ = shot_and_previous(project_id, shot_id, principal)
        media_path = _safe_media_path(media_root, request.media_path)
        reference = _safe_media_path(media_root, request.reference_image_path) if request.reference_image_path else None
        inspection = media_quality.inspect(
            media_path, request.expected_duration_sec, request.expected_width,
            request.expected_height, reference,
        )
        return {"shot_id": shot.id, "inspection": inspection, "metric_source": "decoded-media"}

    @app.post("/api/projects/{project_id}/shots/{shot_id}/approve-keyframe")
    def approve_keyframe(project_id: str, shot_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project, shot, _ = shot_and_previous(project_id, shot_id, principal)
        existing = next((item for item in project.approvals if item.target_type == "keyframe" and item.target_id == shot.id), None)
        if existing:
            return {"approval": existing, "deduplicated": True}
        approval = Approval(id=f"approval_{uuid4().hex[:8]}", target_type="keyframe", target_id=shot.id, status="approved", reviewer=principal.user_id, organization_id=principal.organization_id)
        project.approvals.append(approval)
        add_audit(project, principal, "keyframe.approved", "shot", shot.id)
        store.save(project)
        return {"approval": approval, "deduplicated": False}

    @app.post("/api/projects/{project_id}/quality-run")
    def run_project_quality(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project = _project_or_404(store, project_id, principal.organization_id)
        measured, skipped = [], []
        for run in project.generation_runs:
            shot = next((item for item in project.shots if item.id == run.shot_id), None)
            if not shot or not run.output_uri or run.output_uri.startswith("mock://"):
                skipped.append({"shot_id": run.shot_id, "reason": "no decoded media artifact"})
                continue
            try:
                path = _safe_media_path(media_root, run.output_uri.removeprefix("file://"))
                measured.append({"shot_id": shot.id, "inspection": media_quality.inspect(path, shot.duration_sec)})
            except HTTPException as exc:
                skipped.append({"shot_id": run.shot_id, "reason": str(exc.detail)})
        return {
            "status": "completed" if measured and not skipped else "partial" if measured else "no_media",
            "metric_source": "decoded-media", "measured": measured, "skipped": skipped,
            "message": "Mock results are never treated as measured quality.",
        }

    @app.post("/api/projects/{project_id}/export")
    def create_export_manifest(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "export")
        project = _project_or_404(store, project_id, principal.organization_id)
        export_dir = media_root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        manifest = delivery_service.build_manifest(project, media_root)
        project.delivery_manifests.append(manifest)
        add_audit(project, principal, "delivery.manifest_created", "delivery_manifest", manifest.id, blockers=len(manifest.blockers))
        store.save(project)
        path = export_dir / f"{project.id}.manifest.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return {
            "status": "manifest_only" if manifest.blockers else manifest.status,
            "manifest": manifest,
            "manifest_path": str(path),
            "assembled": False,
        }

    @app.post("/api/projects/{project_id}/workflow")
    async def workflow_command(project_id: str, request: WorkflowCommand, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        project = _project_or_404(store, project_id, principal.organization_id)
        try:
            workflow.command(project, request.command)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if request.command == "cancel":
            for run in project.generation_runs:
                if run.status not in {GenerationStatus.queued, GenerationStatus.running}:
                    continue
                if remote_provider:
                    try:
                        await remote_provider.cancel(run.provider_task_id)
                        task_states.transition(run, TaskLifecycleStatus.cancelled, "Cancelled by workflow", principal.user_id)
                    except Exception as exc:
                        run.retry_count += 1
                        store.save(project)
                        raise HTTPException(502, f"Provider cancellation failed: {exc}") from exc
                else:
                    mock_provider.cancel(run)
        add_audit(project, principal, f"workflow.{request.command}", "project", project.id)
        store.save(project)
        return project

    @app.post("/api/projects/{project_id}/approve-assets")
    def approve_assets(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project = _project_or_404(store, project_id, principal.organization_id)
        for character in project.characters:
            character.locked = True
            for appearance in character.appearances:
                appearance.locked = True
        for scene in project.scenes:
            scene.locked = True
        project.workflow_status, project.workflow_step = WorkflowStatus.ready, 4
        add_audit(project, principal, "asset_bible.approved", "project", project.id)
        store.save(project)
        return {"approved": True, "locked_assets": sum(len(c.appearances) for c in project.characters) + len(project.scenes), "project": project}

    @app.get("/api/me")
    def get_me(principal: Principal = Depends(current_principal)):
        return principal

    @app.get("/api/provider/capabilities")
    def provider_capabilities(principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        if remote_provider is None:
            return {
                "provider": "mock",
                "data_mode": "mock",
                "capabilities": {
                    "media_kinds": ["image", "video"],
                    "supports_reference_image": True,
                    "supports_last_frame": False,
                    "supports_webhook": False,
                    "supports_idempotency_recovery": True,
                    "max_duration_sec": 20,
                },
            }
        return {"provider": provider_name, "data_mode": "configured", "capabilities": remote_provider.capabilities()}

    @app.get("/api/projects/{project_id}/metrics")
    def project_metrics(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        project = _project_or_404(store, project_id, principal.organization_id)
        return operational_metrics(project)

    @app.get("/api/projects/{project_id}/audit-logs")
    def audit_logs(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        project = _project_or_404(store, project_id, principal.organization_id)
        return project.audit_logs

    @app.post("/api/projects/{project_id}/comments", status_code=201)
    def create_comment(project_id: str, request: CommentRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "review")
        project = _project_or_404(store, project_id, principal.organization_id)
        comment = ReviewComment(
            id=f"comment_{uuid4().hex[:10]}", organization_id=project.organization_id,
            project_id=project.id, target_type=request.target_type, target_id=request.target_id,
            body=request.body, author_id=principal.user_id,
        )
        project.comments.append(comment)
        add_audit(project, principal, "comment.created", request.target_type, request.target_id, comment_id=comment.id)
        store.save(project)
        return comment

    @app.post("/api/projects/{project_id}/assets/{logical_asset_id}/versions", status_code=201)
    def create_asset_version(
        project_id: str,
        logical_asset_id: str,
        request: AssetVersionRequest,
        principal: Principal = Depends(current_principal),
    ):
        authorize(principal, "edit")
        project = _project_or_404(store, project_id, principal.organization_id)
        versions = [item for item in project.assets if (item.logical_id or item.id) == logical_asset_id]
        latest = max(versions, key=lambda item: item.version) if versions else None
        asset = Asset(
            id=f"asset_{uuid4().hex[:10]}", project_id=project.id, organization_id=project.organization_id,
            logical_id=logical_asset_id, parent_version_id=latest.id if latest else None,
            asset_type=str(request.metadata.get("asset_type", "reference")), name=request.name,
            uri=request.uri, content_hash=request.content_hash, version=(latest.version + 1 if latest else 1),
            source_uri=request.source_uri, license_name=request.license_name, license_uri=request.license_uri,
            prompt=request.prompt, negative_prompt=request.negative_prompt, metadata=request.metadata,
            created_by=principal.user_id,
        )
        project.assets.append(asset)
        affected = dependencies.invalidate(project, latest.id, f"Asset {logical_asset_id} advanced to v{asset.version}") if latest else []
        affected += dependencies.invalidate(project, logical_asset_id, f"Asset {logical_asset_id} advanced to v{asset.version}")
        add_audit(project, principal, "asset.version_created", "asset", asset.id, logical_id=logical_asset_id, version=asset.version, affected=affected)
        store.save(project)
        return {"asset": asset, "stale_artifacts": list(dict.fromkeys(affected))}

    @app.post("/api/projects/{project_id}/dependencies", status_code=201)
    def create_dependency(project_id: str, request: DependencyRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        project = _project_or_404(store, project_id, principal.organization_id)
        known_upstream = any((item.logical_id or item.id) == request.upstream_asset_id or item.id == request.upstream_asset_id for item in project.assets)
        known_downstream = any(item.id == request.downstream_asset_id for item in project.assets) or any(item.id == request.downstream_asset_id for item in project.shots)
        if not known_upstream or not known_downstream:
            raise HTTPException(422, "Dependency endpoints must reference known project artifacts")
        try:
            dependency = dependencies.add(project, request.upstream_asset_id, request.downstream_asset_id, request.relation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        add_audit(project, principal, "dependency.created", "dependency", dependency.id)
        store.save(project)
        return dependency

    @app.get("/api/projects/{project_id}/storyboard.csv")
    def export_storyboard_csv(project_id: str, principal: Principal = Depends(current_principal)):
        authorize(principal, "read")
        project = _project_or_404(store, project_id, principal.organization_id)
        return Response(
            storyboard_exchange.export_csv(project),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{project.id}-storyboard.csv"'},
        )

    @app.post("/api/projects/{project_id}/storyboard/import")
    def import_storyboard_csv(project_id: str, request: StoryboardImportRequest, principal: Principal = Depends(current_principal)):
        authorize(principal, "edit")
        try:
            project = store.snapshot(project_id, principal.organization_id)
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc
        if project.version != request.expected_project_version:
            raise HTTPException(409, detail={"message": "Project version conflict", "current_version": project.version})
        try:
            count = storyboard_exchange.import_csv(project, request.csv_text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        add_audit(project, principal, "storyboard.csv_imported", "project", project.id, shot_count=count)
        try:
            saved = store.replace(project, request.expected_project_version)
        except Exception as exc:
            raise HTTPException(409, "Project was modified while importing storyboard") from exc
        return {"updated_shots": count, "project_version": saved.version}

    return app


def _project_or_404(store: ProjectStore, project_id: str, organization_id: str | None = None) -> Project:
    try:
        return store.get(project_id, organization_id)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc


def _safe_media_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(400, "Media path must stay within MANGAFLOW_MEDIA_ROOT")
    return resolved


def _generation_state(status: ProviderState) -> GenerationStatus:
    return {
        ProviderState.queued: GenerationStatus.queued,
        ProviderState.running: GenerationStatus.running,
        ProviderState.succeeded: GenerationStatus.completed,
        ProviderState.failed: GenerationStatus.failed,
        ProviderState.cancelled: GenerationStatus.cancelled,
    }[status]


def _task_lifecycle(status: ProviderState) -> TaskLifecycleStatus:
    return {
        ProviderState.queued: TaskLifecycleStatus.queued,
        ProviderState.running: TaskLifecycleStatus.running,
        ProviderState.succeeded: TaskLifecycleStatus.succeeded,
        ProviderState.failed: TaskLifecycleStatus.failed,
        ProviderState.cancelled: TaskLifecycleStatus.cancelled,
    }[status]


def _load_comfy_workflow(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


app = create_app()
