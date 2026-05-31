import { describe, expect, it } from "vitest";

import { buildTranscodeOutputKey } from "@/lib/transcode";

describe("buildTranscodeOutputKey", () => {
  it("preserves directory structure", () => {
    expect(
      buildTranscodeOutputKey("projects/demo/day1/clipA.mov", { container: "mp4" }),
    ).toBe("projects/demo/day1/clipA.mp4");
  });

  it("supports prepend folder", () => {
    expect(
      buildTranscodeOutputKey("projects/demo/day1/clipA.mov", {
        container: "mp4",
        prependFolder: "proxies",
      }),
    ).toBe("proxies/projects/demo/day1/clipA.mp4");
  });

  it("supports append folder", () => {
    expect(
      buildTranscodeOutputKey("projects/demo/day1/clipA.mov", {
        container: "mp4",
        appendFolder: "proxies",
      }),
    ).toBe("projects/demo/day1/proxies/clipA.mp4");
  });
});
