import { describe, expect, it } from "vitest";

import {
  clampUnit,
  coverFit,
  normalizePoint,
  pointFromClient,
  roundUnit,
  svgPathFromOutlinePoints,
  toBoardPixels,
} from "@/panels/blocks/canvas/geometry";

describe("clampUnit / roundUnit / normalizePoint", () => {
  it("clamps into [0, 1] (the worker drops anything outside it, so the page never sends it)", () => {
    expect(clampUnit(-0.4)).toBe(0);
    expect(clampUnit(1.4)).toBe(1);
    expect(clampUnit(0.5)).toBe(0.5);
    expect(clampUnit(Number.NaN)).toBe(0);
  });

  it("rounds to 4 decimals, mirroring the worker's own rounding", () => {
    expect(roundUnit(0.123456)).toBe(0.1235);
  });

  it("normalizePoint clamps and rounds every coordinate, keeping arity", () => {
    expect(normalizePoint([1.4, -0.4])).toEqual([1, 0]);
    expect(normalizePoint([0.123456, 0.5, 1.4])).toEqual([0.1235, 0.5, 1]);
  });
});

describe("pointFromClient", () => {
  const rect = { left: 100, top: 50, width: 200, height: 100 };

  it("normalises a client position to the board's 0..1 space", () => {
    expect(pointFromClient(200, 100, rect)).toEqual([0.5, 0.5]);
    expect(pointFromClient(100, 50, rect)).toEqual([0, 0]);
    expect(pointFromClient(300, 150, rect)).toEqual([1, 1]);
  });

  it("clamps a position outside the board's own rect", () => {
    expect(pointFromClient(0, 0, rect)).toEqual([0, 0]);
    expect(pointFromClient(1000, 1000, rect)).toEqual([1, 1]);
  });

  it("carries pressure only when it is a real, non-default reading (a pen, not a mouse's implicit 0.5)", () => {
    expect(pointFromClient(200, 100, rect, 0.8)).toEqual([0.5, 0.5, 0.8]);
    expect(pointFromClient(200, 100, rect, 0.5)).toEqual([0.5, 0.5]);
    expect(pointFromClient(200, 100, rect, 0)).toEqual([0.5, 0.5]);
    expect(pointFromClient(200, 100, rect, undefined)).toEqual([0.5, 0.5]);
  });

  it("normalises to [0, 0] rather than NaN for a zero-size rect (jsdom, an unmounted node)", () => {
    expect(pointFromClient(50, 50, { left: 0, top: 0, width: 0, height: 0 })).toEqual([0, 0]);
  });
});

describe("toBoardPixels", () => {
  it("scales a normalised point to board pixels", () => {
    expect(toBoardPixels([0.5, 0.25], 1600, 1200)).toEqual([800, 300]);
  });
});

describe("coverFit", () => {
  it("scales content up to cover a wider container, centring the overflow", () => {
    // 400x100 content into a 100x80 container: scale = max(100/400, 80/100) = 0.8.
    expect(coverFit({ w: 100, h: 80 }, { w: 400, h: 100 })).toEqual({ x: -110, y: 0, w: 320, h: 80 });
  });

  it("falls back to filling the container plainly when either size is degenerate", () => {
    expect(coverFit({ w: 100, h: 80 }, { w: 0, h: 0 })).toEqual({ x: 0, y: 0, w: 100, h: 80 });
    expect(coverFit({ w: 0, h: 0 }, { w: 400, h: 100 })).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });
});

describe("svgPathFromOutlinePoints", () => {
  it("is empty for no points", () => {
    expect(svgPathFromOutlinePoints([])).toBe("");
  });

  it("draws a dot (a closed loop) for a single point", () => {
    expect(svgPathFromOutlinePoints([[5, 5]])).toBe("M 5 5 L 5 5");
  });

  it("starts with M at the first point and ends with Z (a closed outline)", () => {
    const d = svgPathFromOutlinePoints([
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
    ]);
    expect(d.startsWith("M 0 0 Q")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
  });
});
