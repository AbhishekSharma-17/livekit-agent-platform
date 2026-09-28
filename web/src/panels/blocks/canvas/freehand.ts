/**
 * `perfect-freehand` outlines for `pen`/`highlighter` strokes (V6-14, D-V6-17: MIT,
 * 1.2.3, ~4.5 KB raw / ~2 KB gzipped, zero runtime dependencies — verified at
 * implementation). Loaded with a dynamic `import()` — on top of the whole `canvas` block
 * already being behind `React.lazy` (`blocks/index.tsx`) — so the library itself is
 * fetched only once a board actually needs to draw a freehand stroke. If the import ever
 * fails (a blocked CDN, an unexpected bundler split) `strokeOutline` falls back to
 * `polylineOutline` (`geometry.ts`) — a plain ribbon with no dependency, per D-V6-17's
 * fallback clause — so the board keeps working either way.
 */
import { polylineOutline, svgPathFromOutlinePoints, type InkPoint } from "./geometry";

type GetStroke = typeof import("perfect-freehand").getStroke;

let getStroke: GetStroke | "failed" | null = null;
let loading: Promise<void> | null = null;

/** Kicks off the dynamic import once; safe to call more than once (idempotent). */
export function preloadFreehand(): Promise<void> {
  if (getStroke !== null) return Promise.resolve();
  loading ??= import("perfect-freehand")
    .then((mod) => {
      getStroke = mod.getStroke as GetStroke;
    })
    .catch(() => {
      getStroke = "failed";
    });
  return loading;
}

/** Whether the real library is ready (`false` before the first `preloadFreehand`, or if it failed). */
export function freehandReady(): boolean {
  return typeof getStroke === "function";
}

/**
 * A stroke's outline as board-pixel points, in a `[x, y]`-only form ready for
 * `svgPathFromOutlinePoints`/`rasterise.ts`. `points` are normalised 0..1 (a caller
 * stroke's own points); `toPixels` converts one to board pixels (`geometry.ts`'s
 * `toBoardPixels`, bound to the board's own width/height by the caller).
 */
export function strokeOutlinePoints(
  points: readonly InkPoint[],
  toPixels: (point: InkPoint) => [number, number],
  width: number,
): [number, number][] {
  const pixelPoints = points.map(toPixels);
  if (!freehandReady()) return polylineOutline(pixelPoints, width);
  const hasPressure = points.some((p) => p.length === 3);
  const outline = (getStroke as GetStroke)(
    points.map((p, i) => {
      const [x, y] = pixelPoints[i];
      return p.length === 3 ? [x, y, p[2]] : [x, y];
    }),
    { size: width, thinning: 0.6, smoothing: 0.5, streamline: 0.5, simulatePressure: !hasPressure },
  );
  if (outline.length === 0) return polylineOutline(pixelPoints, width);
  return outline.map(([x, y]) => [x, y]);
}

/** An SVG `d` attribute for one stroke, freehand when the library is ready, a ribbon otherwise. */
export function strokePathD(points: readonly InkPoint[], toPixels: (point: InkPoint) => [number, number], width: number): string {
  return svgPathFromOutlinePoints(strokeOutlinePoints(points, toPixels, width));
}
