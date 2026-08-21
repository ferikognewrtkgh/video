import { Project } from "./types";

export function isMockProject(project: Project, apiOnline: boolean): boolean {
  return !apiOnline || project.data_mode === "demo" || project.generation_runs.some(run => run.provider === "mock");
}

export function metricLabel(mock: boolean): string {
  return mock ? "MOCK RESULT · NOT MEASURED" : "MEASURED FROM MEDIA";
}

