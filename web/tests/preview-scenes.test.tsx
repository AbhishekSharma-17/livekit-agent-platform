import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { SCENES, resolveSceneParams, sceneListPayload } from "@/components/preview/scenes";

/**
 * The preview scene registry (docs/UI_UX_SPEC.md §7.11 item 5): every scene
 * renders from defaulted params alone (no room, no next/font) and contains
 * the `data-testid`s it promises. This imports the registry directly, never
 * the route or the layout, because `next/font` only runs under Next's
 * compiler (`src/panels/README.md`).
 */
describe("preview scene registry", () => {
  it("has at least the scenes the spec names", () => {
    expect(Object.keys(SCENES)).toEqual(
      expect.arrayContaining([
        "session",
        "notebook",
        "generic",
        "precall",
        "ended",
        "unavailable",
        "primitives",
        // V2-11
        "blocks",
        "composite",
      ]),
    );
  });

  for (const scene of Object.values(SCENES)) {
    it(`renders "${scene.id}" with its default params and contains its testIds`, () => {
      const params = resolveSceneParams(scene.id, {});
      const { container } = render(<>{scene.render(params)}</>);
      for (const testId of scene.testIds) {
        expect(
          container.querySelector(`[data-testid="${testId}"]`),
          `expected [data-testid="${testId}"] in scene "${scene.id}"`,
        ).not.toBeNull();
      }
    });

    it(`renders every combo of "${scene.id}" without throwing`, () => {
      for (const combo of scene.combos) {
        const params = resolveSceneParams(scene.id, combo.params);
        expect(() => render(<>{scene.render(params)}</>)).not.toThrow();
      }
    });
  }

  it("resolveSceneParams falls back to defaults for missing or invalid values", () => {
    const params = resolveSceneParams("session", { layout: "not-a-layout" });
    expect(params.layout).toBe(SCENES.session.defaults.layout);
    expect(params.state).toBe(SCENES.session.defaults.state);
  });

  it("resolveSceneParams returns {} for an unknown scene", () => {
    expect(resolveSceneParams("does-not-exist", {})).toEqual({});
  });

  it("sceneListPayload has one row per combo, with a non-empty query and scene id", () => {
    const payload = sceneListPayload();
    const totalCombos = Object.values(SCENES).reduce((sum, scene) => sum + scene.combos.length, 0);
    expect(payload).toHaveLength(totalCombos);
    for (const entry of payload) {
      expect(entry.scene).toBeTruthy();
      expect(entry.query).toContain(`scene=${entry.scene}`);
      expect(entry.surfaces.length).toBeGreaterThan(0);
    }
  });

  it("V2-11: the blocks scene shoots every block type filled and empty, plus a submitted form", () => {
    const names = SCENES.blocks.combos.map((combo) => combo.name);
    expect(names).toHaveLength(25);
    expect(names).toContain("form-submitted");
    expect(names).toContain("document-filled");
    expect(names).toContain("kb_citations-empty");
  });
});
