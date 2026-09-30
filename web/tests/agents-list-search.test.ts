import { describe, expect, it } from "vitest";

import { matchRanges, matchesQuery, normalizeText, queryWords } from "@/components/shared/list-search";

/** The agents list's search cases, on the one shared list search (docs/ui/DESIGN-SYSTEM.md section 9). */
describe("list search (docs/ui/DESIGN-SYSTEM.md section 9)", () => {
  it.each([
    ["Café Désk", "cafe desk"],
    ["ÅNGSTRÖM", "angstrom"],
    ["plain", "plain"],
  ])("folds %s to %s", (input, expected) => {
    expect(normalizeText(input)).toBe(expected);
  });

  it("splits a query into folded words and ignores blanks", () => {
    expect(queryWords("  Café   DESK ")).toEqual(["cafe", "desk"]);
    expect(queryWords("   ")).toEqual([]);
  });

  it("needs every word to match somewhere, across fields", () => {
    const fields = ["Café concierge", "cafe-line"];
    expect(matchesQuery(fields, "cafe line")).toBe(true);
    expect(matchesQuery(fields, "cafe desk")).toBe(false);
    expect(matchesQuery(fields, "")).toBe(true);
    expect(matchesQuery([null, undefined], "x")).toBe(false);
  });

  it("maps matches back to the original text, merging overlaps", () => {
    expect(matchRanges("Café Café", "cafe")).toEqual([
      [0, 4],
      [5, 9],
    ]);
    expect(matchRanges("support desk", "sup port")).toEqual([[0, 7]]);
    expect(matchRanges("anything", "")).toEqual([]);
  });
});
