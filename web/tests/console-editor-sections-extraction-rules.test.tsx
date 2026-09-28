import { describe, expect, it } from "vitest";

/**
 * V6-15: the Extraction and Rules sections register in `editor/sections.ts` (the actual
 * registry file — the plan card's `agents/editor-sections.ts` is a stale path; ratified in
 * `docs/v6/_asks.md`), between the built-in "Tools" (50) and "Knowledge" (60) sections,
 * with "Tests" (85) still last (`console-editor-sections.test.tsx`'s own pin).
 */
describe("EDITOR_SECTIONS (V6-15)", () => {
  it("registers Extraction and Rules with their own issue paths, without disturbing Tests as the last section", async () => {
    const { EDITOR_SECTIONS } = await import("@/components/console/agents/editor/sections");
    const ids = EDITOR_SECTIONS.map((section) => section.id);

    const extraction = EDITOR_SECTIONS.find((section) => section.id === "extraction");
    expect(extraction?.label).toBe("Extraction");
    expect(extraction?.issuePaths).toEqual(["extraction"]);

    const rules = EDITOR_SECTIONS.find((section) => section.id === "rules");
    expect(rules?.label).toBe("Rules");
    expect(rules?.issuePaths).toEqual(["rules"]);

    // Both sit after "tools" and before "knowledge" in the nav order.
    expect(ids.indexOf("tools")).toBeLessThan(ids.indexOf("extraction"));
    expect(ids.indexOf("extraction")).toBeLessThan(ids.indexOf("rules"));
    expect(ids.indexOf("rules")).toBeLessThan(ids.indexOf("knowledge"));

    expect(ids[ids.length - 1]).toBe("tests");
  });
});
