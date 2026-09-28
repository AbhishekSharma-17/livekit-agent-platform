import { describe, expect, it, vi } from "vitest";

import {
  drawBoard,
  rasteriseBoard,
  MAX_CANVAS_SNAPSHOT_BYTES,
  type Raster2DContext,
  type RasterBoard,
  type RasterCanvas,
} from "@/panels/blocks/canvas/rasterise";

/**
 * V6-14, ask #92: rasterising a board to the PNG `read_canvas` reads. jsdom has no real 2D
 * context (or `Path2D`), so `rasterise.ts` only calls the handful of `CanvasRenderingContext2D`
 * primitives `Raster2DContext` names — this fakes exactly those and asserts the calls made,
 * the same way `panel-blocks.test.tsx` stubs pdf.js for the `document` block.
 */

function fakeContext(): Raster2DContext & { calls: string[] } {
  const calls: string[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textBaseline: "alphabetic" as CanvasTextBaseline,
    calls,
    fillRect: (x: number, y: number, w: number, h: number) => calls.push(`fillRect ${x},${y},${w},${h}`),
    drawImage: (_image: CanvasImageSource, x: number, y: number, w: number, h: number) => calls.push(`drawImage ${x},${y},${w},${h}`),
    beginPath: () => calls.push("beginPath"),
    closePath: () => calls.push("closePath"),
    moveTo: (x: number, y: number) => calls.push(`moveTo ${x},${y}`),
    lineTo: (x: number, y: number) => calls.push(`lineTo ${x},${y}`),
    quadraticCurveTo: (cpx: number, cpy: number, x: number, y: number) => calls.push(`quadraticCurveTo ${cpx},${cpy},${x},${y}`),
    ellipse: (x: number, y: number, rx: number, ry: number) => calls.push(`ellipse ${x},${y},${rx},${ry}`),
    rect: (x: number, y: number, w: number, h: number) => calls.push(`rect ${x},${y},${w},${h}`),
    fill: () => calls.push("fill"),
    stroke: () => calls.push("stroke"),
    fillText: (text: string, x: number, y: number) => calls.push(`fillText ${text} ${x},${y}`),
  };
  return ctx;
}

function fakeCanvas(ctx: Raster2DContext, blob: Blob | null = new Blob(["png"], { type: "image/png" })): RasterCanvas {
  return {
    width: 0,
    height: 0,
    getContext: () => ctx,
    toBlob: (callback) => callback(blob),
  };
}

const EMPTY_BOARD: RasterBoard = { width: 100, height: 80, background: { kind: "none" }, strokes: [], shapes: [] };

describe("drawBoard", () => {
  it("fills a white background when there is none", () => {
    const ctx = fakeContext();
    drawBoard(ctx, EMPTY_BOARD);
    expect(ctx.calls).toContain("fillRect 0,0,100,80");
  });

  it("cover-fits an image background into the board (matches the live board's fit)", () => {
    const ctx = fakeContext();
    const source = {} as CanvasImageSource;
    drawBoard(ctx, { ...EMPTY_BOARD, background: { kind: "image", source, naturalSize: { w: 400, h: 100 } } });
    // 400x100 into 100x80: scale = max(100/400, 80/100) = 0.8 -> 320x80, centred horizontally.
    expect(ctx.calls).toContain("drawImage -110,0,320,80");
  });

  it("draws a pen stroke as a filled, closed outline (fill, not stroke)", () => {
    const ctx = fakeContext();
    drawBoard(ctx, {
      ...EMPTY_BOARD,
      strokes: [{ points: [[0.1, 0.1], [0.5, 0.5], [0.9, 0.1]], color: "#1f2937", width: 3 }],
    });
    expect(ctx.calls).toContain("beginPath");
    expect(ctx.calls).toContain("closePath");
    expect(ctx.calls).toContain("fill");
    expect(ctx.calls.some((c) => c.startsWith("quadraticCurveTo"))).toBe(true);
    expect(ctx.calls).not.toContain("stroke");
  });

  it("draws a box shape as a stroked rectangle from its normalised x,y,w,h", () => {
    const ctx = fakeContext();
    drawBoard(ctx, { ...EMPTY_BOARD, shapes: [{ kind: "box", x: 0.1, y: 0.2, w: 0.3, h: 0.25, color: "#dc2626", width: 4 }] });
    expect(ctx.calls).toContain("rect 10,16,30,20");
    expect(ctx.calls).toContain("stroke");
  });

  it("draws an arrow shape with a shaft and a two-line head, from its two points", () => {
    const ctx = fakeContext();
    drawBoard(ctx, {
      ...EMPTY_BOARD,
      shapes: [{ kind: "arrow", points: [[0.1, 0.1], [0.9, 0.1]], color: "#dc2626", width: 4 }],
    });
    expect(ctx.calls).toContain("moveTo 10,8");
    expect(ctx.calls).toContain("lineTo 90,8");
    expect(ctx.calls).toContain("stroke");
  });

  it("draws a text shape with fillText, never stroke", () => {
    const ctx = fakeContext();
    drawBoard(ctx, { ...EMPTY_BOARD, shapes: [{ kind: "text", x: 0.1, y: 0.1, text: "the dent", color: "#dc2626", width: 4 }] });
    expect(ctx.calls.some((c) => c.startsWith("fillText the dent"))).toBe(true);
  });

  it("draws strokes before shapes (agent marks read on top)", () => {
    const ctx = fakeContext();
    drawBoard(ctx, {
      ...EMPTY_BOARD,
      strokes: [{ points: [[0.1, 0.1], [0.2, 0.2]], color: "#1f2937", width: 3 }],
      shapes: [{ kind: "box", x: 0.1, y: 0.1, w: 0.1, h: 0.1, color: "#dc2626", width: 4 }],
    });
    const fillIndex = ctx.calls.indexOf("fill");
    const rectIndex = ctx.calls.indexOf("rect 10,8,10,8");
    expect(fillIndex).toBeGreaterThanOrEqual(0);
    expect(rectIndex).toBeGreaterThan(fillIndex);
  });

  it("does nothing for a box/circle shape missing a dimension (a still-in-progress or malformed shape)", () => {
    const ctx = fakeContext();
    drawBoard(ctx, { ...EMPTY_BOARD, shapes: [{ kind: "box", x: 0.1, y: null, color: "#dc2626", width: 4 }] });
    expect(ctx.calls).not.toContain("stroke");
  });
});

describe("rasteriseBoard", () => {
  it("resolves the PNG blob from the injected canvas factory", async () => {
    const ctx = fakeContext();
    const blob = new Blob(["fake-png-bytes"], { type: "image/png" });
    const createCanvas = vi.fn(() => fakeCanvas(ctx, blob));
    const result = await rasteriseBoard(EMPTY_BOARD, { createCanvas });
    expect(createCanvas).toHaveBeenCalledWith(100, 80);
    expect(result).toBe(blob);
  });

  it("resolves null when the canvas has no 2D context", async () => {
    const createCanvas = () => ({ width: 0, height: 0, getContext: () => null, toBlob: vi.fn() }) as RasterCanvas;
    const result = await rasteriseBoard(EMPTY_BOARD, { createCanvas });
    expect(result).toBeNull();
  });

  it("MAX_CANVAS_SNAPSHOT_BYTES matches the api's 5 MiB cap (CONTRACTS §10)", () => {
    expect(MAX_CANVAS_SNAPSHOT_BYTES).toBe(5 * 1024 * 1024);
  });
});
