import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * "Layout usable at 375 px" (V6-19's acceptance) for the three new dialogs (upload, Add-kit,
 * dataset tool editor): a source scan, not a rendered-viewport test (jsdom has no real
 * layout), for the one thing that actually breaks a narrow screen — a hard-coded pixel/rem
 * width on the dialog or one of its top-level sections (`w-[420px]`, say). The small,
 * already-reviewed pattern of a fixed Tailwind scale width on one control inside a
 * `flex-wrap` row (`w-40` on a `Select`, as the HTTP/provider tool editors already do) isn't
 * what this catches — that wraps fine; a literal pixel/rem width on a layout container does not.
 */

const FILES = [
  "src/components/console/datasets/upload-dataset-dialog.tsx",
  "src/components/console/tools/kits/add-kit-dialog.tsx",
  "src/components/console/tools/dataset-tool-editor-dialog.tsx",
];

const FIXED_WIDTH_RE = /\bw-\[\d+(?:\.\d+)?(?:px|rem)\]/;

describe("V6-19 dialogs stay usable at 375 px", () => {
  for (const file of FILES) {
    it(`${file} has no hard-coded pixel/rem width`, () => {
      const text = readFileSync(path.resolve(__dirname, "..", file), "utf8");
      const match = text.match(FIXED_WIDTH_RE);
      expect(match, `found ${match?.[0]} in ${file}`).toBeNull();
    });
  }
});
