/**
 * Rasterising a board to a PNG for `read_canvas`'s snapshot answer (V6-14, ask #92;
 * CONTRACTS §10: "render background + strokes + shapes to a PNG ≤ 5 MiB and stream it on
 * `lkap.ui.upload`"). Pure enough to unit-test: the canvas element itself is **injectable**
 * (`createCanvas`) — jsdom has no real 2D context (and no `Path2D`), so `Raster2DContext`
 * only names the handful of `CanvasRenderingContext2D` primitives this module calls
 * (`beginPath`/`moveTo`/`lineTo`/`quadraticCurveTo`/`ellipse`/`rect`/`fill`/`stroke`/
 * `fillText`/`drawImage`/`fillRect`) — a test can fake just those and assert the calls
 * made, the same way `blocks/document.tsx`'s pdf.js is stubbed in `panel-blocks.test.tsx`.
 *
 * The freehand outline recipe here (`fillOutline`) mirrors `geometry.ts`'s
 * `svgPathFromOutlinePoints` exactly, one in canvas calls and the other in an SVG `d`
 * string, so a pen stroke looks the same in the PNG as it does live. The background fit
 * **must** also match the live SVG board's (`background.tsx`'s `coverFit` call) — an
 * agent's `draw_on_canvas` shape is normalised to the board, not to the picture behind it,
 * so if the picture were cropped differently in the snapshot than on screen, "the dent"
 * the caller circled and the ellipse the agent drew would stop lining up.
 */
import { arrowGeometry, coverFit, ellipseAttrs, toBoardPixels, type InkPoint } from "./geometry";
import { strokeOutlinePoints } from "./freehand";

export interface RasterStroke {
  points: readonly InkPoint[];
  color: string;
  width: number;
}

export interface RasterShape {
  kind: "box" | "circle" | "arrow" | "text" | "path";
  x?: number | null;
  y?: number | null;
  w?: number | null;
  h?: number | null;
  points?: readonly [number, number][];
  text?: string | null;
  label?: string | null;
  /** Defaults mirror `CanvasShape`'s own (`DEFAULT_SHAPE_COLOR`, width 4) — the contract leaves both optional. */
  color?: string;
  width?: number;
}

export interface RasterBackground {
  /** `none` draws no picture layer; `image`/`video` draw `source` cover-fit to the board. */
  kind: "none" | "image" | "video";
  source?: CanvasImageSource;
  /** The source's own pixel size, for `coverFit` — required when `kind` is not `none`. */
  naturalSize?: { w: number; h: number };
}

export interface RasterBoard {
  width: number;
  height: number;
  background: RasterBackground;
  strokes: readonly RasterStroke[];
  shapes: readonly RasterShape[];
}

/** The slice of `CanvasRenderingContext2D` this module calls — deliberately narrow (see file docblock). */
export interface Raster2DContext {
  fillStyle: string | CanvasGradient | CanvasPattern;
  strokeStyle: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  font: string;
  textBaseline: CanvasTextBaseline;
  fillRect(x: number, y: number, w: number, h: number): void;
  drawImage(image: CanvasImageSource, dx: number, dy: number, dw: number, dh: number): void;
  beginPath(): void;
  closePath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  quadraticCurveTo(cpx: number, cpy: number, x: number, y: number): void;
  ellipse(x: number, y: number, radiusX: number, radiusY: number, rotation: number, startAngle: number, endAngle: number): void;
  rect(x: number, y: number, w: number, h: number): void;
  fill(): void;
  stroke(): void;
  fillText(text: string, x: number, y: number): void;
}

export interface RasterCanvas {
  width: number;
  height: number;
  getContext(kind: "2d"): Raster2DContext | null;
  toBlob(callback: (blob: Blob | null) => void, type?: string, quality?: number): void;
}

/** The default factory: a real offscreen `<canvas>` (browser-only; a test injects its own). */
export function createDomCanvas(width: number, height: number): RasterCanvas {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas as unknown as RasterCanvas;
}

function toPixels(width: number, height: number) {
  return (point: InkPoint): [number, number] => toBoardPixels(point, width, height);
}

function drawBackground(ctx: Raster2DContext, board: RasterBoard): void {
  const { background, width, height } = board;
  if (background.kind === "none" || !background.source) {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
    return;
  }
  const natural = background.naturalSize ?? { w: width, h: height };
  const fit = coverFit({ w: width, h: height }, natural);
  ctx.drawImage(background.source, fit.x, fit.y, fit.w, fit.h);
}

/** The `perfect-freehand` recipe (`geometry.ts::svgPathFromOutlinePoints`) in canvas calls: a smoothed, closed outline through each edge's midpoint. */
function fillOutline(ctx: Raster2DContext, points: readonly [number, number][]): void {
  if (points.length === 0) return;
  ctx.beginPath();
  if (points.length === 1) {
    const [x, y] = points[0];
    ctx.moveTo(x, y);
    ctx.lineTo(x, y);
  } else {
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 0; i < points.length; i += 1) {
      const [x0, y0] = points[i];
      const [x1, y1] = points[(i + 1) % points.length];
      ctx.quadraticCurveTo(x0, y0, (x0 + x1) / 2, (y0 + y1) / 2);
    }
  }
  ctx.closePath();
  ctx.fill();
}

function drawStroke(ctx: Raster2DContext, stroke: RasterStroke, width: number, height: number): void {
  const outline = strokeOutlinePoints(stroke.points, toPixels(width, height), stroke.width);
  ctx.fillStyle = stroke.color;
  fillOutline(ctx, outline);
}

const DEFAULT_SHAPE_COLOR = "#dc2626";
const DEFAULT_SHAPE_WIDTH = 4;

function drawShape(ctx: Raster2DContext, shape: RasterShape, width: number, height: number): void {
  const color = shape.color ?? DEFAULT_SHAPE_COLOR;
  const strokeWidth = shape.width ?? DEFAULT_SHAPE_WIDTH;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = strokeWidth;
  switch (shape.kind) {
    case "box": {
      if (shape.x == null || shape.y == null || shape.w == null || shape.h == null) return;
      ctx.beginPath();
      ctx.rect(shape.x * width, shape.y * height, shape.w * width, shape.h * height);
      ctx.stroke();
      return;
    }
    case "circle": {
      if (shape.x == null || shape.y == null || shape.w == null || shape.h == null) return;
      const { cx, cy, rx, ry } = ellipseAttrs(shape.x * width, shape.y * height, shape.w * width, shape.h * height);
      ctx.beginPath();
      ctx.ellipse(cx, cy, Math.max(rx, 0.01), Math.max(ry, 0.01), 0, 0, Math.PI * 2);
      ctx.stroke();
      return;
    }
    case "arrow": {
      const points = shape.points ?? [];
      if (points.length !== 2) return;
      const from: [number, number] = [points[0][0] * width, points[0][1] * height];
      const to: [number, number] = [points[1][0] * width, points[1][1] * height];
      const { leftWing, rightWing } = arrowGeometry(from, to, strokeWidth);
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.lineTo(to[0], to[1]);
      ctx.moveTo(leftWing[0], leftWing[1]);
      ctx.lineTo(to[0], to[1]);
      ctx.lineTo(rightWing[0], rightWing[1]);
      ctx.stroke();
      return;
    }
    case "path": {
      const points = (shape.points ?? []).map(([x, y]): [number, number] => [x * width, y * height]);
      if (points.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(points[0][0], points[0][1]);
      for (const [x, y] of points.slice(1)) ctx.lineTo(x, y);
      ctx.stroke();
      return;
    }
    case "text": {
      if (shape.x == null || shape.y == null || !shape.text) return;
      ctx.font = `${Math.max(12, strokeWidth * 4)}px sans-serif`;
      ctx.textBaseline = "top";
      ctx.fillText(shape.text, shape.x * width, shape.y * height);
      return;
    }
  }
}

/** Draws one board onto `ctx` (background, then every stroke, then every shape — agent marks read on top). */
export function drawBoard(ctx: Raster2DContext, board: RasterBoard): void {
  drawBackground(ctx, board);
  for (const stroke of board.strokes) drawStroke(ctx, stroke, board.width, board.height);
  for (const shape of board.shapes) drawShape(ctx, shape, board.width, board.height);
}

export interface RasteriseOptions {
  /** Injected for tests; defaults to a real `<canvas>` (`createDomCanvas`). */
  createCanvas?: (width: number, height: number) => RasterCanvas;
  mimeType?: string;
  quality?: number;
}

/** The largest snapshot the api accepts (`MAX_CANVAS_SNAPSHOT_BYTES`, CONTRACTS §10). */
export const MAX_CANVAS_SNAPSHOT_BYTES = 5 * 1024 * 1024;

/** Renders `board` and resolves the PNG `Blob`, or `null` if the canvas has no 2D context or produced nothing. */
export function rasteriseBoard(board: RasterBoard, options: RasteriseOptions = {}): Promise<Blob | null> {
  const createCanvas = options.createCanvas ?? createDomCanvas;
  const canvas = createCanvas(board.width, board.height);
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  drawBoard(ctx, board);
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), options.mimeType ?? "image/png", options.quality);
  });
}
