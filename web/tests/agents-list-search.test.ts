import { describe, expect, it } from "vitest";

import { foldText, matchRanges, matchesAllWords, queryWords } from "@/components/console/agents/list-search";

describe("list search (docs/ui/DESIGN-SYSTEM.md section 9)", () => {
  it.each([
    ["Café Désk", "cafe desk"],
    ["ÅNGSTRÖM", "angstrom"],
    ["plain", "plain"],
  ])("folds %s to %s", (input, expected) => {
    expect(foldText(input)).toBe(expected);
  });

  it("splits a query into folded words and ignores blanks", () => {
    expect(queryWords("  Café   DESK ")).toEqual(["cafe", "desk"]);
    expect(queryWords("   ")).toEqual([]);
  });

  it("needs every word to match somewhere, across fields", () => {
    const fields = ["Café concierge", "cafe-line"];
    expect(matchesAllWords(["cafe", "line"], fields)).toBe(true);
    expect(matchesAllWords(["cafe", "desk"], fields)).toBe(false);
    expect(matchesAllWords([], fields)).toBe(true);
    expect(matchesAllWords(["x"], [null, undefined])).toBe(false);
  });

  it("maps matches back to the original text, merging overlaps", () => {
    expect(matchRanges("Café Café", ["cafe"])).toEqual([
      [0, 4],
      [5, 9],
    ]);
    expect(matchRanges("support desk", ["sup", "port"])).toEqual([[0, 7]]);
    expect(matchRanges("anything", [])).toEqual([]);
  });
});
