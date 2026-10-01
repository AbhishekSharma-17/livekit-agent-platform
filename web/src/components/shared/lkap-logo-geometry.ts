/**
 * The LKAP logomark's geometry, the one source for the `LkapLogo` component
 * and the app icons (`src/app/icon.svg`, the PNGs and `favicon.ico`, written by
 * `scripts/gen-app-icons.mjs`). Plain data with no imports, so the Node script
 * can load this file directly.
 *
 * On a 32 x 32 grid: a rounded tile in `--brand`, and in `--brand-foreground`
 * a "live" dot followed by two rising bars, a voice starting to speak. Bars are
 * 4 units wide with 2.25-unit gaps so the mark still reads at 16 px, and the
 * group is centred on the tile.
 */
export const LKAP_MARK = {
  size: 32,
  /** Corner radius of the tile (a quarter of its side). */
  radius: 8,
  dot: { cx: 9.75, cy: 16, r: 3.25 },
  bars: [
    { x: 15.25, y: 10.5, width: 4, height: 11 },
    { x: 21.5, y: 7.5, width: 4, height: 17 },
  ],
} as const;

/** Fill colours for a standalone icon file, which cannot read CSS variables. */
export interface LkapIconColors {
  tile: string;
  ink: string;
}

/**
 * The mark as a standalone SVG document. `radius: 0` gives a full-bleed square
 * for the Apple touch icon, which iOS masks with its own corners.
 */
export function lkapIconSvg(colors: LkapIconColors, { radius = LKAP_MARK.radius }: { radius?: number } = {}): string {
  const { size, dot, bars } = LKAP_MARK;
  const bar = (b: (typeof bars)[number]) =>
    `<rect x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" rx="${b.width / 2}" fill="${colors.ink}"/>`;
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}">`,
    `<title>LKAP</title>`,
    `<rect width="${size}" height="${size}" rx="${radius}" fill="${colors.tile}"/>`,
    `<circle cx="${dot.cx}" cy="${dot.cy}" r="${dot.r}" fill="${colors.ink}"/>`,
    ...bars.map(bar),
    `</svg>`,
    "",
  ].join("\n");
}
