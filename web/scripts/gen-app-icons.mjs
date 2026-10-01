#!/usr/bin/env node
/**
 * Writes the app icons from the LKAP mark's one geometry
 * (`src/components/shared/lkap-logo-geometry.ts`) and the light brand colours
 * mirrored in `src/lib/browser-colors.ts`:
 *
 * - `src/app/icon.svg`, the vector favicon (Next links it automatically);
 * - `src/app/favicon.ico`, 16, 32 and 48 px PNGs for browsers without SVG
 *   favicons (Safari);
 * - `src/app/apple-icon.png`, 180 px and full bleed, since iOS draws its own
 *   rounded corners;
 * - `public/icon-192.png` and `public/icon.png` (512 px), the manifest icons.
 *
 * The PNGs are committed, so nothing renders at build or request time. Run
 * `node scripts/gen-app-icons.mjs` from `web/` after changing the mark or the
 * brand colours. `--check` only compares `icon.svg` with the geometry (PNG
 * bytes vary with the rasteriser version), and `tests/app-icons.test.ts` runs
 * the same comparison plus a size check of every file.
 *
 * Rasterising uses sharp, which Next already depends on. It is not a direct
 * dependency of this package, so it is resolved from Next's own location.
 * Node strips the types from the two `.ts` modules on import.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { BROWSER_COLORS } from "../src/lib/browser-colors.ts";
import { lkapIconSvg } from "../src/components/shared/lkap-logo-geometry.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = (file) => path.join(root, file);

export const COLORS = { tile: BROWSER_COLORS.light.brand, ink: BROWSER_COLORS.light.brandForeground };
export const FAVICON_SIZES = [16, 32, 48];

/** An ICO file holding one PNG per size (supported by every current browser). */
export function icoFromPngs(entries) {
  const header = Buffer.alloc(6 + 16 * entries.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(entries.length, 4);
  let offset = header.length;
  entries.forEach(({ size, png }, index) => {
    const at = 6 + 16 * index;
    header.writeUInt8(size >= 256 ? 0 : size, at);
    header.writeUInt8(size >= 256 ? 0 : size, at + 1);
    header.writeUInt8(0, at + 2);
    header.writeUInt8(0, at + 3);
    header.writeUInt16LE(1, at + 4);
    header.writeUInt16LE(32, at + 6);
    header.writeUInt32LE(png.length, at + 8);
    header.writeUInt32LE(offset, at + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...entries.map((entry) => entry.png)]);
}

async function main() {
  const svg = lkapIconSvg(COLORS);
  if (process.argv.includes("--check")) {
    if (readFileSync(out("src/app/icon.svg"), "utf8") !== svg) {
      console.error("src/app/icon.svg is out of date: run `node scripts/gen-app-icons.mjs`");
      process.exit(1);
    }
    console.log("src/app/icon.svg matches the LKAP mark");
    return;
  }
  const sharp = createRequire(createRequire(import.meta.url).resolve("next/package.json"))("sharp");
  const png = (source, size) =>
    sharp(Buffer.from(source), { density: (72 * size) / 32 })
      .resize(size, size)
      .png({ compressionLevel: 9 })
      .toBuffer();

  writeFileSync(out("src/app/icon.svg"), svg);
  const favicons = await Promise.all(FAVICON_SIZES.map(async (size) => ({ size, png: await png(svg, size) })));
  writeFileSync(out("src/app/favicon.ico"), icoFromPngs(favicons));
  writeFileSync(out("src/app/apple-icon.png"), await png(lkapIconSvg(COLORS, { radius: 0 }), 180));
  writeFileSync(out("public/icon-192.png"), await png(svg, 192));
  writeFileSync(out("public/icon.png"), await png(svg, 512));
  console.log("wrote src/app/icon.svg, src/app/favicon.ico, src/app/apple-icon.png, public/icon-192.png, public/icon.png");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
