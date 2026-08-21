from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, Protocol

from .demo import build_demo_project
from .domain import Project, utc_now


class VersionConflict(RuntimeError):
    pass


class ProjectRepository(Protocol):
    def list(self, organization_id: str | None = None) -> list[Project]: ...
    def get(self, project_id: str, organization_id: str | None = None) -> Project: ...
    def put(self, project: Project, persist: bool = True) -> Project: ...
    def save(self, project: Project) -> None: ...
    def replace(self, project: Project, expected_version: int) -> Project: ...


class ProjectStore:
    """Thread-safe JSON repository with atomic checkpoints and startup recovery."""

    def __init__(self, checkpoint_dir: Path, seed_demo: bool = True) -> None:
        self._lock = RLock()
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._projects: Dict[str, Project] = {}
        self.reload()
        if seed_demo and "project_afterimage" not in self._projects:
            demo = build_demo_project()
            demo.data_mode = "demo"
            self._projects[demo.id] = demo

    def reload(self) -> int:
        loaded: Dict[str, Project] = {}
        with self._lock:
            for path in self.checkpoint_dir.glob("*.json"):
                try:
                    project = Project.model_validate_json(path.read_text(encoding="utf-8"))
                    loaded[project.id] = project
                except (OSError, ValueError):
                    # A broken checkpoint must not prevent the service from starting.
                    continue
            self._projects.update(loaded)
        return len(loaded)

    def list(self, organization_id: str | None = None) -> list[Project]:
        with self._lock:
            return [
                project.model_copy(deep=True)
                for project in self._projects.values()
                if organization_id is None or project.organization_id == organization_id
            ]

    def get(self, project_id: str, organization_id: str | None = None) -> Project:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(project_id)
            project = self._projects[project_id]
            if organization_id is not None and project.organization_id != organization_id:
                # Do not reveal that another tenant's project exists.
                raise KeyError(project_id)
            return project

    def snapshot(self, project_id: str, organization_id: str | None = None) -> Project:
        with self._lock:
            return self.get(project_id, organization_id).model_copy(deep=True)

    def put(self, project: Project, persist: bool = True) -> Project:
        with self._lock:
            project.updated_at = utc_now()
            self._projects[project.id] = project
            if persist:
                self._write_atomic(project)
            return project

    def save(self, project: Project) -> None:
        with self._lock:
            current = self._projects.get(project.id)
            if current is not None:
                project.version = current.version + 1
            self.put(project, persist=True)

    def replace(self, project: Project, expected_version: int) -> Project:
        """Persist a detached snapshot using optimistic concurrency control."""
        with self._lock:
            current = self._projects.get(project.id)
            if current is None:
                raise KeyError(project.id)
            if current.organization_id != project.organization_id:
                raise KeyError(project.id)
            if current.version != expected_version:
                raise VersionConflict(f"Expected project version {expected_version}, found {current.version}")
            replacement = project.model_copy(deep=True)
            replacement.version = expected_version + 1
            replacement.updated_at = utc_now()
            self._projects[replacement.id] = replacement
            self._write_atomic(replacement)
            return replacement.model_copy(deep=True)

    def restore_runs(self) -> Iterable[Project]:
        """Return projects that had unfinished work when the process stopped."""
        with self._lock:
            return [
                project for project in self._projects.values()
                if any(run.lifecycle_status.value in {"created", "queued", "submitting", "submitted", "running", "unknown"} for run in project.generation_runs)
            ]

    def _write_atomic(self, project: Project) -> None:
        destination = self.checkpoint_dir / f"{project.id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(destination)
