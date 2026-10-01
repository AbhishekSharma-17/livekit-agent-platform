import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";
import { lkapIconSvg } from "@/components/shared/lkap-logo-geometry";
import { BROWSER_COLORS } from "@/lib/browser-colors";

/**
 * The app icons (scripts/gen-app-icons.mjs): drawn from the same geometry as
 * `LkapLogo`, in the light brand pair mirrored in `lib/browser-colors.ts`, so
 * the favicon, the touch icon, the manifest icons and the in-app mark agree.
 */
const web = path.resolve(__dirname, "..");
const read = (file: string) => readFileSync(path.join(web, file));

/** Width and height from a PNG's IHDR chunk. */
function pngSize(png: Buffer): [number, number] {
  expect(png.subarray(1, 4).toString("latin1")).toBe("PNG");
  return [png.readUInt32BE(16), png.readUInt32BE(20)];
}

describe("app icons", () => {
  it("icon.svg is the LKAP mark in the light brand colours (rerun scripts/gen-app-icons.mjs when it changes)", () => {
    const svg = read("src/app/icon.svg").toString("utf8");
    expect(svg).toBe(lkapIconSvg({ tile: BROWSER_COLORS.light.brand, ink: BROWSER_COLORS.light.brandForeground }));
    expect(svg).toContain(`fill="${BROWSER_COLORS.light.brand}"`);
    expect(svg).toContain(`fill="${BROWSER_COLORS.light.brandForeground}"`);
  });

  it("favicon.ico holds 16, 32 and 48 px PNGs", () => {
    const ico = read("src/app/favicon.ico");
    expect(ico.readUInt16LE(2)).toBe(1);
    const count = ico.readUInt16LE(4);
    const sizes = Array.from({ length: count }, (_, i) => {
      const at = 6 + 16 * i;
      const png = ico.subarray(ico.readUInt32LE(at + 12), ico.readUInt32LE(at + 12) + ico.readUInt32LE(at + 8));
      expect(pngSize(png)).toEqual([ico.readUInt8(at), ico.readUInt8(at + 1)]);
      return ico.readUInt8(at);
    });
    expect(sizes).toEqual([16, 32, 48]);
  });

  it("apple-icon.png is 180 px", () => {
    expect(pngSize(read("src/app/apple-icon.png"))).toEqual([180, 180]);
  });

  it("every manifest icon exists at its declared size", () => {
    const icons = manifest().icons ?? [];
    expect(icons.map((icon) => icon.sizes)).toEqual(["192x192", "512x512"]);
    for (const icon of icons) {
      const file = path.join("public", icon.src);
      expect(existsSync(path.join(web, file)), file).toBe(true);
      const [w, h] = pngSize(read(file));
      expect(`${w}x${h}`).toBe(icon.sizes);
      expect(icon.purpose).toBeUndefined();
    }
  });
});
