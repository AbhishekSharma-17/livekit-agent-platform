import { describe, expect, it } from "vitest";

import {
  DEFAULT_ASPECT_RATIO,
  classifyAspect,
  resolveFrame,
} from "@/components/session/avatar-framing";

describe("classifyAspect", () => {
  it("classifies a 9:16 track as portrait", () => {
    expect(classifyAspect(720, 1280)).toBe("portrait");
  });
  it("classifies a 1:1 track as square", () => {
    expect(classifyAspect(600, 600)).toBe("square");
  });
  it("classifies a 16:9 track as landscape", () => {
    expect(classifyAspect(1280, 720)).toBe("landscape");
  });
  it("is auto for a zero dimension", () => {
    expect(classifyAspect(0, 0)).toBe("auto");
  });
});

describe("resolveFrame — fit and focal point", () => {
  it("defaults to contain with a centered position (V6-26 item 2)", () => {
    const frame = resolveFrame({});
    expect(frame.objectFit).toBe("contain");
    expect(frame.objectPosition).toBe("50% 50%");
  });

  it("cover biases the crop to the upper third, never the center", () => {
    const frame = resolveFrame({ fit: "cover" });
    expect(frame.objectFit).toBe("cover");
    expect(frame.objectPosition).toBe("50% 33%");
  });

  it("an expand toggle (forceCover) overrides an explicit contain choice", () => {
    const frame = resolveFrame({ fit: "contain", forceCover: true });
    expect(frame.objectFit).toBe("cover");
  });
});

describe("resolveFrame — aspect precedence (V6-26 item 1/3)", () => {
  it("a stored agent with nothing set (no framing, no fit, no declaredAspect, no measurement) renders the pre-V6-26 16:9 guess with contain — crop-free default", () => {
    const frame = resolveFrame({});
    expect(frame.aspectRatio).toBeCloseTo(DEFAULT_ASPECT_RATIO);
    expect(frame.objectFit).toBe("contain");
    expect(frame.measured).toBe(false);
  });

  it("an explicit framing wins over nothing else known", () => {
    const frame = resolveFrame({ framing: "portrait" });
    expect(frame.aspectRatio).toBeCloseTo(9 / 16);
  });

  it("the provider's declared aspect is used when framing is auto/unset", () => {
    const frame = resolveFrame({ framing: "auto", declaredAspect: "portrait" });
    expect(frame.aspectRatio).toBeCloseTo(9 / 16);
  });

  it("an explicit framing overrides the provider's declared aspect", () => {
    const frame = resolveFrame({ framing: "square", declaredAspect: "portrait" });
    expect(frame.aspectRatio).toBe(1);
  });

  it("the real measured aspect always wins, even over an explicit framing", () => {
    const frame = resolveFrame({
      framing: "landscape",
      measured: { width: 720, height: 1280 },
    });
    expect(frame.aspectRatio).toBeCloseTo(720 / 1280);
    expect(frame.measured).toBe(true);
  });

  it("no jump: a declared aspect's pre-connect guess matches the real measurement once it arrives, for portrait/square/landscape", () => {
    for (const [declared, dims] of [
      ["portrait", { width: 9, height: 16 }],
      ["square", { width: 500, height: 500 }],
      ["landscape", { width: 16, height: 9 }],
    ] as const) {
      const before = resolveFrame({ declaredAspect: declared });
      const after = resolveFrame({ declaredAspect: declared, measured: dims });
      expect(after.aspectRatio).toBeCloseTo(before.aspectRatio, 5);
    }
  });

  it("a provider with no declared aspect (auto) still avoids a jump into a wildly different guess by falling back to the 16:9 default, and adopts the real measurement once it arrives", () => {
    const before = resolveFrame({});
    expect(before.aspectRatio).toBeCloseTo(DEFAULT_ASPECT_RATIO);
    const after = resolveFrame({ measured: { width: 720, height: 1280 } });
    expect(after.aspectRatio).toBeCloseTo(9 / 16);
  });
});
