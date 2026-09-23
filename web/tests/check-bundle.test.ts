import { describe, expect, it } from "vitest";

import { firstLoadFromLog } from "../scripts/check-bundle.mjs";

/** A `next build` route table as Next 15.5 prints it (trimmed). */
const TABLE = `
Route (app)                                 Size  First Load JS
┌ ƒ /                                    1.83 kB         190 kB
├ ƒ /console/preview/panels              18.8 kB         590 kB
├ ○ /login                               6.55 kB         121 kB
└ ƒ /s/[slug]                            65.4 kB         589 kB
+ First Load JS shared by all             103 kB
`;

/** R-V2-18: the budget is the First Load JS column of `/s/[slug]`. */
describe("check-bundle firstLoadFromLog", () => {
  it("reads the First Load JS column, not Size", () => {
    expect(firstLoadFromLog(TABLE, "/s/[slug]")).toBe(589);
  });

  it("matches the route exactly (not a prefix of another route)", () => {
    expect(firstLoadFromLog(TABLE, "/")).toBe(190);
    expect(firstLoadFromLog(TABLE, "/s")).toBeNull();
  });

  it("normalises MB and strips ANSI colours", () => {
    const coloured = "└ ƒ /s/[slug]   \u001b[33m65.4 kB\u001b[39m   \u001b[31m1.2 MB\u001b[39m";
    expect(firstLoadFromLog(coloured, "/s/[slug]")).toBe(1200);
  });

  it("returns null when the route is missing, so the CLI never passes silently", () => {
    expect(firstLoadFromLog("Compiled successfully", "/s/[slug]")).toBeNull();
  });
});
