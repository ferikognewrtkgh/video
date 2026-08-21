import { MediaArtifact, Project } from "./types";

export function isMockProject(project: Project, apiOnline: boolean): boolean {
  return !apiOnline || project.data_mode === "demo" || project.generation_runs.some(run => run.provider === "mock");
}

export function metricLabel(mock: boolean): string {
  return mock ? "MOCK RESULT · NOT MEASURED" : "MEASURED FROM MEDIA";
}

export function realArtifactsForShot(project: Project, shotId: string, kind: MediaArtifact["kind"]): MediaArtifact[] {
  return (project.media_artifacts||[]).filter(item=>
    item.shot_id===shotId && item.kind===kind && item.source!=="mock" && item.measured
  );
}

export function latestFinalArtifact(project: Project): MediaArtifact|undefined {
  return [...(project.media_artifacts||[])].reverse().find(item=>
    item.kind==="final_video" && item.source!=="mock" && item.measured
  );
}
