import { describe, expect, it } from "vitest";

import {
  activeJobConflictMessage,
  canOpenRenderOutput,
  shouldApplyPollUpdate,
} from "@/lib/render-job-poll";
import type { RenderJobStatus } from "@/lib/types";

function job(
  overrides: Partial<RenderJobStatus> & Pick<RenderJobStatus, "id" | "status">,
): RenderJobStatus {
  return {
    job: {
      output_path: "outputs/output.mp4",
    },
    worker_payload: {},
    report: null,
    ...overrides,
  };
}

describe("shouldApplyPollUpdate", () => {
  it("prefers remote active state over local terminal state for the same job", () => {
    const local = job({
      id: "job-1",
      status: "succeeded",
      output: { path: "s3://bucket/output.mp4" },
      updated_at: "2026-05-28T12:00:00Z",
    });
    const remote = job({
      id: "job-1",
      status: "running",
      updated_at: "2026-05-28T12:01:00Z",
    });

    expect(shouldApplyPollUpdate(local, remote)).toBe(true);
  });

  it("applies newer terminal remote updates", () => {
    const local = job({
      id: "job-1",
      status: "running",
      updated_at: "2026-05-28T12:00:00Z",
    });
    const remote = job({
      id: "job-1",
      status: "succeeded",
      output: { path: "s3://bucket/output.mp4" },
      updated_at: "2026-05-28T12:02:00Z",
    });

    expect(shouldApplyPollUpdate(local, remote)).toBe(true);
  });
});

describe("canOpenRenderOutput", () => {
  it("requires succeeded status and output path", () => {
    expect(
      canOpenRenderOutput(
        job({
          id: "job-1",
          status: "succeeded",
          output: { path: "s3://bucket/output.mp4" },
        }),
      ),
    ).toBe(true);
    expect(
      canOpenRenderOutput(
        job({
          id: "job-1",
          status: "succeeded",
          output: { path: "" },
        }),
      ),
    ).toBe(false);
    expect(
      canOpenRenderOutput(
        job({
          id: "job-1",
          status: "running",
        }),
      ),
    ).toBe(false);
  });
});

describe("activeJobConflictMessage", () => {
  it("includes the active status when known", () => {
    expect(
      activeJobConflictMessage(
        job({
          id: "job-1",
          status: "running",
        }),
      ),
    ).toContain("status: running");
  });
});
