import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { demoProject } from "../lib/demo-data";
import { isMockProject, latestFinalArtifact, metricLabel, realArtifactsForShot } from "../lib/presentation";
import { MockBadge } from "./MockBadge";

describe("mock result disclosure", () => {
  it("renders an explicit warning for demo data", () => {
    const html = renderToStaticMarkup(<MockBadge visible={isMockProject(demoProject, true)} />);
    expect(html).toContain("DEMO DATA / MOCK RESULT");
  });

  it("does not render for measured non-mock projects", () => {
    const measured = { ...demoProject, data_mode: "generated" as const, generation_runs: [] };
    expect(renderToStaticMarkup(<MockBadge visible={isMockProject(measured, true)} />)).toBe("");
    expect(metricLabel(false)).toBe("MEASURED FROM MEDIA");
  });

  it("shows only measured real artifacts in review and delivery", () => {
    const realKeyframe = { id:"real-keyframe", shot_id:"shot_03", kind:"keyframe" as const, storage_path:"real.png", mime_type:"image/png", checksum_sha256:"a".repeat(64), file_size:100, source:"provider", review_status:"pending" as const, measured:true, metadata:{} };
    const mockKeyframe = { ...realKeyframe, id:"mock-keyframe", source:"mock" };
    const finalVideo = { ...realKeyframe, id:"final", shot_id:undefined, kind:"final_video" as const, storage_path:"final.mp4", mime_type:"video/mp4", source:"ffmpeg", review_status:"not_required" as const };
    const project = { ...demoProject, media_artifacts:[mockKeyframe,realKeyframe,finalVideo] };
    expect(realArtifactsForShot(project,"shot_03","keyframe").map(item=>item.id)).toEqual(["real-keyframe"]);
    expect(latestFinalArtifact(project)?.id).toBe("final");
  });
});
