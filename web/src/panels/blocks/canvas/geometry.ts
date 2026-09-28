/**
 * Pure geometry for the drawing board (V6-14, D-V6-16): coordinate normalisation, shape
 * paths and the outline-to-SVG-path recipe. No React, no DOM API beyond what the caller
 * hands in (a `DOMRect`) — shared by the live SVG board and the PNG rasteriser
 * (`rasterise.ts`) so an agent's mark lands on the same spot in both.
 */

/** A caller stroke point: `[x, y]` or `[x, y, pressure]`, each normalised 0..1. */
export type InkPoint = [number, number] | [number, number, number];

/** Clamp `value` into `[0, 1]` (the worker does the same for a point it accepts). */
export function clampUnit(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Round to 4 decimals — a tenth of a pixel on a 1,600 px board — mirrors the worker. */
export function roundUnit(value: number): number {
  return Math.round(value * 10000) / 10000;
}

/** Clamp and round one point, keeping its arity (2 or 3 numbers). */
export function normalizePoint(point: InkPoint): InkPoint {
  return point.map((value) => roundUnit(clampUnit(value))) as InkPoint;
}

/**
 * A pointer event's page position turned into a board-normalised point. `rect` is the
 * board SVG's `getBoundingClientRect()`; a zero-size rect (jsdom, an unmounted node)
 * normalises to `[0, 0]` rather than `NaN`. `pressure` is carried only when the device
 * reports a real one (a pen); a mouse's implicit `0.5` is dropped so the wire point stays
 * a plain `[x, y]` (`ask #92`: "pressure only when the device reports one").
 */
export function pointFromClient(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  pressure?: number,
): InkPoint {
  const x = rect.width > 0 ? (clientX - rect.left) / rect.width : 0;
  const y = rect.height > 0 ? (clientY - rect.top) / rect.height : 0;
  const base: InkPoint = [roundUnit(clampUnit(x)), roundUnit(clampUnit(y))];
  if (typeof pressure === "number" && pressure > 0 && pressure !== 0.5) {
    return [base[0], base[1], roundUnit(clampUnit(pressure))];
  }
  return base;
}

/** A normalised point to board pixels (`CanvasBlockState.width`/`.height`). */
export function toBoardPixels(point: InkPoint, width: number, height: number): [number, number] {
  return [point[0] * width, point[1] * height];
}

export interface FitRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * `object-fit: cover`'s rectangle: `content` (its own aspect ratio) scaled and centred to
 * fill `container` exactly, cropping the overflow. The same formula draws a `live_camera`
 * background in the live SVG (a `<foreignObject>`/absolutely-positioned `<video>`, sized
 * by CSS) and in the PNG rasteriser's `drawImage` — the two must agree, or an agent's
 * shape ("circle the dent") lands off the picture the caller sees (ask review).
 */
export function coverFit(container: { w: number; h: number }, content: { w: number; h: number }): FitRect {
  if (content.w <= 0 || content.h <= 0 || container.w <= 0 || container.h <= 0) {
    return { x: 0, y: 0, w: container.w, h: container.h };
  }
  const scale = Math.max(container.w / content.w, container.h / content.h);
  const w = content.w * scale;
  const h = content.h * scale;
  return { x: (container.w - w) / 2, y: (container.h - h) / 2, w, h };
}

/** An SVG path `d` for a rectangle (`box`/`circle`'s bounding box), in board pixels. */
export function rectPath(x: number, y: number, w: number, h: number): string {
  return `M ${x} ${y} H ${x + w} V ${y + h} H ${x} Z`;
}

/** An SVG `<ellipse>`'s attributes for a `circle` shape's bounding box `x,y,w,h`. */
export function ellipseAttrs(x: number, y: number, w: number, h: number): { cx: number; cy: number; rx: number; ry: number } {
  return { cx: x + w / 2, cy: y + h / 2, rx: w / 2, ry: h / 2 };
}

/** Length of an arrow's head, proportional to its stroke width (never wider than the shaft is long). */
function headLength(width: number, shaftLength: number): number {
  return Math.min(Math.max(width * 2.5, 8), Math.max(shaftLength / 2, 8));
}

export interface ArrowGeometry {
  from: [number, number];
  to: [number, number];
  /** The head's two wing tips, each met back at `to`. */
  leftWing: [number, number];
  rightWing: [number, number];
}

/** The shaft and arrowhead wings for an arrow from `from` to `to` (board pixels) — shared by the SVG path builder and the canvas rasteriser, so the two draw the same head. */
export function arrowGeometry(from: [number, number], to: [number, number], width: number): ArrowGeometry {
  const [x0, y0] = from;
  const [x1, y1] = to;
  const dx = x1 - x0;
  const dy = y1 - y0;
  const length = Math.hypot(dx, dy) || 1;
  const angle = Math.atan2(dy, dx);
  const head = headLength(width, length);
  const spread = Math.PI / 7;
  return {
    from,
    to,
    leftWing: [x1 - head * Math.cos(angle - spread), y1 - head * Math.sin(angle - spread)],
    rightWing: [x1 - head * Math.cos(angle + spread), y1 - head * Math.sin(angle + spread)],
  };
}

/** An SVG path `d` for an arrow from `[x0,y0]` to `[x1,y1]` (board pixels): a shaft plus a head. */
export function arrowPath(from: [number, number], to: [number, number], width: number): string {
  const { leftWing, rightWing } = arrowGeometry(from, to, width);
  return `M ${from[0]} ${from[1]} L ${to[0]} ${to[1]} M ${leftWing[0]} ${leftWing[1]} L ${to[0]} ${to[1]} L ${rightWing[0]} ${rightWing[1]}`;
}

/** An SVG path `d` through a series of board-pixel points (a `path` shape, or a straight box/arrow guide). */
export function polylinePath(points: [number, number][]): string {
  if (points.length === 0) return "";
  const [first, ...rest] = points;
  return `M ${first[0]} ${first[1]} ` + rest.map(([x, y]) => `L ${x} ${y}`).join(" ");
}

/**
 * The standard `perfect-freehand` recipe: an outline polygon (points already ordered
 * around the shape) turned into a smoothed, closed SVG path via quadratic curves through
 * each edge's midpoint. Also the polyline fallback's finishing step (`freehand.ts`) when
 * the library did not load — a plain, less-smoothed ribbon is still a valid path here.
 */
export function svgPathFromOutlinePoints(points: readonly [number, number][]): string {
  if (points.length === 0) return "";
  if (points.length === 1) {
    const [x, y] = points[0];
    return `M ${x} ${y} L ${x} ${y}`;
  }
  const parts: (string | number)[] = ["M", points[0][0], points[0][1], "Q"];
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    parts.push(x0, y0, (x0 + x1) / 2, (y0 + y1) / 2);
  }
  parts.push("Z");
  return parts.join(" ");
}

/**
 * A flat ribbon around a polyline (no `perfect-freehand`): each segment offset by half the
 * stroke width on both sides, joined into one outline. Deliberately simple — this only
 * runs when the dynamic import of the real library fails (D-V6-17's fallback), so a
 * slightly blockier line is an acceptable trade for "the board still works with no
 * dependency at all".
 */
export function polylineOutline(points: readonly [number, number][], width: number): [number, number][] {
  if (points.length === 0) return [];
  if (points.length === 1) {
    const [x, y] = points[0];
    const r = width / 2;
    return [
      [x - r, y - r],
      [x + r, y - r],
      [x + r, y + r],
      [x - r, y + r],
    ];
  }
  const half = width / 2;
  const left: [number, number][] = [];
  const right: [number, number][] = [];
  for (let i = 0; i < points.length; i += 1) {
    const [x, y] = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];
    const dx = next[0] - prev[0];
    const dy = next[1] - prev[1];
    const len = Math.hypot(dx, dy) || 1;
    const nx = (-dy / len) * half;
    const ny = (dx / len) * half;
    left.push([x + nx, y + ny]);
    right.push([x - nx, y - ny]);
  }
  return [...left, ...right.reverse()];
}
