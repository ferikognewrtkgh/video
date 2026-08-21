from __future__ import annotations

from threading import RLock
from typing import Dict

from .demo import build_demo_project
from .domain import Project


class ProjectStore:
    def __init__(self) -> None:
        self._lock = RLock()
        demo = build_demo_project()
        self._projects: Dict[str, Project] = {demo.id: demo}

    def list(self):
        with self._lock:
            return list(self._projects.values())

    def get(self, project_id: str) -> Project:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(project_id)
            return self._projects[project_id]

    def put(self, project: Project) -> Project:
        with self._lock:
            self._projects[project.id] = project
            return project


store = ProjectStore()

