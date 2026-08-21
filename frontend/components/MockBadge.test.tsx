import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { demoProject } from "../lib/demo-data";
import { isMockProject, metricLabel } from "../lib/presentation";
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
});

