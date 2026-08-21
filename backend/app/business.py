from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from typing import Dict, Iterable
from uuid import uuid4

from .domain import (
    ArtifactDependency,
    AuditLog,
    DeliveryManifest,
    DeliveryTrack,
    GenerationRun,
    GenerationStatus,
    Membership,
    MediaKind,
    Principal,
    Project,
    RetryClass,
    Role,
    TaskLifecycleStatus,
    TaskTransition,
    utc_now,
)


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


PERMISSIONS = {
    Role.owner: {"read", "edit", "generate", "review", "export", "admin"},
    Role.producer: {"read", "edit", "generate", "review", "export"},
    Role.creator: {"read", "edit", "generate"},
    Role.reviewer: {"read", "review"},
    Role.viewer: {"read"},
}


class IdentityDirectory:
    """Server-side membership lookup; callers never provide their own role."""

    def __init__(self, memberships: Iterable[Membership] | None = None) -> None:
        seed = memberships or (
            Membership(organization_id="org_demo", user_id="user_owner", role=Role.owner),
            Membership(organization_id="org_demo", user_id="user_producer", role=Role.producer),
            Membership(organization_id="org_demo", user_id="user_creator", role=Role.creator),
            Membership(organization_id="org_demo", user_id="user_reviewer", role=Role.reviewer),
            Membership(organization_id="org_demo", user_id="user_viewer", role=Role.viewer),
            Membership(organization_id="org_other", user_id="user_other", role=Role.owner),
        )
        self._memberships: Dict[tuple[str, str], Membership] = {
            (item.organization_id, item.user_id): item for item in seed
        }

    def authenticate(self, organization_id: str, user_id: str) -> Principal:
        membership = self._memberships.get((organization_id, user_id))
        if membership is None:
            raise AuthenticationError("Unknown or inactive organization membership")
        return Principal(
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            role=membership.role,
        )

    @staticmethod
    def require(principal: Principal, permission: str) -> None:
        if permission not in PERMISSIONS[principal.role]:
            raise AuthorizationError(f"Role {principal.role.value} lacks {permission} permission")


class InvalidTaskTransition(ValueError):
    pass


class GenerationStateMachine:
    ALLOWED = {
        TaskLifecycleStatus.created: {
            TaskLifecycleStatus.queued,
            TaskLifecycleStatus.submitting,
            TaskLifecycleStatus.cancelled,
        },
        TaskLifecycleStatus.queued: {
            TaskLifecycleStatus.submitting,
            TaskLifecycleStatus.running,
            TaskLifecycleStatus.succeeded,
            TaskLifecycleStatus.failed,
            TaskLifecycleStatus.cancelled,
            TaskLifecycleStatus.unknown,
        },
        TaskLifecycleStatus.submitting: {
            TaskLifecycleStatus.submitted,
            TaskLifecycleStatus.unknown,
            TaskLifecycleStatus.failed,
            TaskLifecycleStatus.cancelled,
        },
        TaskLifecycleStatus.submitted: {
            TaskLifecycleStatus.queued,
            TaskLifecycleStatus.running,
            TaskLifecycleStatus.succeeded,
            TaskLifecycleStatus.failed,
            TaskLifecycleStatus.cancelled,
            TaskLifecycleStatus.unknown,
        },
        TaskLifecycleStatus.running: {
            TaskLifecycleStatus.succeeded,
            TaskLifecycleStatus.failed,
            TaskLifecycleStatus.cancelled,
            TaskLifecycleStatus.unknown,
        },
        TaskLifecycleStatus.unknown: {
            TaskLifecycleStatus.queued,
            TaskLifecycleStatus.running,
            TaskLifecycleStatus.succeeded,
            TaskLifecycleStatus.failed,
            TaskLifecycleStatus.cancelled,
        },
        TaskLifecycleStatus.succeeded: set(),
        TaskLifecycleStatus.failed: set(),
        TaskLifecycleStatus.cancelled: set(),
    }

    LEGACY = {
        TaskLifecycleStatus.created: GenerationStatus.queued,
        TaskLifecycleStatus.queued: GenerationStatus.queued,
        TaskLifecycleStatus.submitting: GenerationStatus.queued,
        TaskLifecycleStatus.submitted: GenerationStatus.queued,
        TaskLifecycleStatus.running: GenerationStatus.running,
        TaskLifecycleStatus.succeeded: GenerationStatus.completed,
        TaskLifecycleStatus.failed: GenerationStatus.failed,
        TaskLifecycleStatus.cancelled: GenerationStatus.cancelled,
        TaskLifecycleStatus.unknown: GenerationStatus.queued,
    }

    def transition(
        self,
        run: GenerationRun,
        target: TaskLifecycleStatus,
        reason: str = "",
        actor: str = "system",
    ) -> GenerationRun:
        current = run.lifecycle_status
        if target == current:
            return run
        if target not in self.ALLOWED[current]:
            raise InvalidTaskTransition(f"Cannot transition generation task from {current.value} to {target.value}")
        run.timeline.append(TaskTransition(from_status=current, to_status=target, reason=reason, actor=actor))
        run.lifecycle_status = target
        run.status = self.LEGACY[target]
        run.updated_at = utc_now()
        return run

    def mark_unknown(self, run: GenerationRun, error: Exception) -> GenerationRun:
        self.transition(run, TaskLifecycleStatus.unknown, "Provider outcome is ambiguous after timeout")
        run.error_code = "PROVIDER_OUTCOME_UNKNOWN"
        run.error_message = str(error)
        run.retry_class = RetryClass.manual_review
        return run


class DependencyService:
    def add(self, project: Project, upstream: str, downstream: str, relation: str) -> ArtifactDependency:
        if upstream == downstream:
            raise ValueError("An artifact cannot depend on itself")
        if any(d.upstream_asset_id == upstream and d.downstream_asset_id == downstream for d in project.artifact_dependencies):
            raise ValueError("Dependency already exists")
        dependency = ArtifactDependency(
            id=f"dep_{uuid4().hex[:10]}",
            upstream_asset_id=upstream,
            downstream_asset_id=downstream,
            relation=relation,
        )
        project.artifact_dependencies.append(dependency)
        return dependency

    def invalidate(self, project: Project, upstream: str, reason: str) -> list[str]:
        queue = [upstream]
        visited: set[str] = set()
        affected: list[str] = []
        now = utc_now()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for dependency in project.artifact_dependencies:
                if dependency.upstream_asset_id != current:
                    continue
                dependency.invalidated = True
                dependency.invalidated_reason = reason
                dependency.invalidated_at = now
                downstream = dependency.downstream_asset_id
                affected.append(downstream)
                queue.append(downstream)
                shot = next((item for item in project.shots if item.id == downstream), None)
                if shot:
                    shot.status = "stale"
                    if reason not in shot.stale_reasons:
                        shot.stale_reasons.append(reason)
        return list(dict.fromkeys(affected))


CSV_FIELDS = ("id", "order", "title", "duration_sec", "shot_type", "shot_size", "camera_motion", "action", "dialogue", "status", "assignee_id")


class StoryboardExchange:
    def export_csv(self, project: Project) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for shot in sorted(project.shots, key=lambda item: item.order):
            writer.writerow({field: getattr(shot, field) or "" for field in CSV_FIELDS})
        return output.getvalue()

    def import_csv(self, project: Project, text: str) -> int:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or not {"id", "order", "title", "duration_sec", "action"}.issubset(reader.fieldnames):
            raise ValueError("CSV must contain id, order, title, duration_sec and action columns")
        by_id = {shot.id: shot for shot in project.shots}
        updates = []
        seen_orders: set[int] = set()
        for row in reader:
            shot = by_id.get((row.get("id") or "").strip())
            if shot is None:
                raise ValueError(f"Unknown shot id: {row.get('id')}")
            try:
                order = int(row["order"])
                duration = float(row["duration_sec"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid number in shot {shot.id}") from exc
            if order in seen_orders or not 0 < duration <= 20:
                raise ValueError(f"Invalid order or duration in shot {shot.id}")
            seen_orders.add(order)
            updates.append((shot, row, order, duration))
        if len(updates) != len(project.shots):
            raise ValueError("CSV must include every project shot exactly once")
        for shot, row, order, duration in updates:
            shot.order = order
            shot.duration_sec = duration
            for field in ("title", "shot_type", "shot_size", "camera_motion", "action", "dialogue", "assignee_id"):
                if field in row:
                    setattr(shot, field, (row.get(field) or "").strip() or None if field == "assignee_id" else (row.get(field) or "").strip())
        return len(updates)


class DeliveryService:
    PRESET = {"width": 1080, "height": 1920, "fps": 24, "video_codec": "h264", "audio_codec": "aac"}

    def build_manifest(self, project: Project, media_root: Path) -> DeliveryManifest:
        final = next((item for item in reversed(project.media_artifacts) if item.kind == MediaKind.final_video), None)
        if final:
            return DeliveryManifest(
                id=f"delivery_{uuid4().hex[:10]}", organization_id=project.organization_id,
                project_id=project.id, status="assembled", assembled=True, preset=self.PRESET,
                tracks=[DeliveryTrack(
                    kind="final_video", uri=f"artifact://{final.id}", checksum_sha256=final.checksum_sha256,
                    file_size=final.file_size, ready=True,
                )],
            )
        blockers: list[str] = []
        tracks: list[DeliveryTrack] = []
        for shot in sorted(project.shots, key=lambda item: item.order):
            artifact = next((
                item for item in reversed(project.media_artifacts)
                if item.kind == MediaKind.video and item.shot_id == shot.id
            ), None)
            if artifact:
                tracks.append(DeliveryTrack(
                    kind="video", uri=f"artifact://{artifact.id}", checksum_sha256=artifact.checksum_sha256,
                    file_size=artifact.file_size, ready=True,
                ))
                continue
            run = next((item for item in reversed(project.generation_runs) if item.shot_id == shot.id and item.status == GenerationStatus.completed), None)
            if run is None or not run.output_uri or run.output_uri.startswith("mock://"):
                blockers.append(f"{shot.id}: missing completed local media")
                tracks.append(DeliveryTrack(kind="video", uri=run.output_uri if run else None, ready=False))
                continue
            raw = run.output_uri.removeprefix("file://")
            path = Path(raw) if Path(raw).is_absolute() else media_root / raw
            path = path.resolve()
            if path != media_root and media_root not in path.parents or not path.is_file():
                blockers.append(f"{shot.id}: media file is unavailable")
                tracks.append(DeliveryTrack(kind="video", uri=run.output_uri, ready=False))
                continue
            tracks.append(DeliveryTrack(
                kind="video",
                uri=str(path),
                checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                file_size=path.stat().st_size,
                ready=True,
            ))
        return DeliveryManifest(
            id=f"delivery_{uuid4().hex[:10]}",
            organization_id=project.organization_id,
            project_id=project.id,
            status="ready_for_assembly" if not blockers else "blocked",
            assembled=False,
            preset=self.PRESET,
            tracks=tracks,
            blockers=blockers,
        )


def add_audit(project: Project, principal: Principal, action: str, target_type: str, target_id: str, **detail) -> AuditLog:
    record = AuditLog(
        id=f"audit_{uuid4().hex[:10]}",
        organization_id=project.organization_id,
        project_id=project.id,
        actor_id=principal.user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )
    project.audit_logs.append(record)
    return record


def operational_metrics(project: Project) -> dict:
    reports = project.qa_reports
    first_pass = [report for report in reports if report.attempt == 0]
    completed = [shot for shot in project.shots if shot.status == "completed"]
    reworked = {report.shot_id for report in reports if report.attempt > 0 or report.failures}
    costs = sum(item.amount for item in project.cost_events)
    unknown = sum(run.lifecycle_status == TaskLifecycleStatus.unknown for run in project.generation_runs)
    return {
        "metric_source": "operational-records",
        "shot_first_pass_rate": round(sum(r.hard_gate_passed and r.score >= 75 for r in first_pass) / len(first_pass), 4) if first_pass else None,
        "rework_rate": round(len(reworked) / len(project.shots), 4) if project.shots else 0,
        "average_cost_per_completed_shot": round(costs / len(completed), 4) if completed else None,
        "completed_duration_sec": round(sum(item.duration_sec for item in completed), 2),
        "budget_used": round(costs, 4),
        "budget_limit": project.budget_limit,
        "unknown_task_count": unknown,
        "stale_shot_count": sum(item.status == "stale" for item in project.shots),
    }
