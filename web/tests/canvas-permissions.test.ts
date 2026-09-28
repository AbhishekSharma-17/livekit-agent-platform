import { describe, expect, it } from "vitest";

import type { BlockSpec } from "@/contracts/lkap-contracts";
import { canvasCallerCanDraw, canvasMaxStrokes, canvasSignatureMode, canvasTools } from "@/panels/blocks/canvas/permissions";

/**
 * V6-14: `canvasCallerCanDraw` is a TypeScript port of
 * `lkap_contracts.blocks.canvas_caller_can_draw` — the board's own `caller_can_draw`, or a
 * notebook `ink` section's, for a board it claims. Mirrors `contracts/tests/
 * test_notebook_layout_v6_08.py`'s cases so the two stay in lockstep.
 */

function block(id: string, type: BlockSpec["type"], config: Record<string, unknown> = {}): BlockSpec {
  return { id, type, config } as BlockSpec;
}

describe("canvasCallerCanDraw", () => {
  it("true when the board's own caller_can_draw is on", () => {
    const blocks = [block("board", "canvas", { caller_can_draw: true })];
    expect(canvasCallerCanDraw("board", blocks)).toBe(true);
  });

  it("false when the board's own caller_can_draw is off and no notebook claims it", () => {
    const blocks = [block("board", "canvas", { caller_can_draw: false })];
    expect(canvasCallerCanDraw("board", blocks)).toBe(false);
  });

  it("true when a notebook's ink section claims the board and the notebook allows drawing", () => {
    const blocks = [
      block("board", "canvas", { caller_can_draw: false }),
      block("nb", "notebook", {
        sections: [{ id: "sketch", kind: "ink", canvas_block_id: "board" }],
        caller_can_draw: true,
      }),
    ];
    expect(canvasCallerCanDraw("board", blocks)).toBe(true);
  });

  it("false when the claiming notebook itself has caller_can_draw off", () => {
    const blocks = [
      block("board", "canvas", { caller_can_draw: false }),
      block("nb", "notebook", {
        sections: [{ id: "sketch", kind: "ink", canvas_block_id: "board" }],
        caller_can_draw: false,
      }),
    ];
    expect(canvasCallerCanDraw("board", blocks)).toBe(false);
  });

  it("the board's own flag wins even without a hosting notebook", () => {
    const blocks = [
      block("board", "canvas", { caller_can_draw: true }),
      block("nb", "notebook", { sections: [{ id: "sketch", kind: "ink", canvas_block_id: "other" }], caller_can_draw: false }),
    ];
    expect(canvasCallerCanDraw("board", blocks)).toBe(true);
  });

  it("false for an id that is not a canvas block, or that does not exist", () => {
    const blocks = [block("gallery", "gallery"), block("board", "canvas", { caller_can_draw: true })];
    expect(canvasCallerCanDraw("gallery", blocks)).toBe(false);
    expect(canvasCallerCanDraw("nope", blocks)).toBe(false);
  });

  it("matches the Notebook preset's own wiring (ask #94): the board's own flag, not the notebook's", () => {
    const blocks = [
      block("status", "status"),
      block("notebook", "notebook", {
        sections: [
          { id: "notes", kind: "text" },
          { id: "sketch", kind: "ink", canvas_block_id: "sketch_board" },
        ],
        caller_can_write: true,
        caller_can_draw: false,
      }),
      block("sketch_board", "canvas", { caller_can_draw: true }),
      block("gallery", "gallery"),
    ];
    expect(canvasCallerCanDraw("sketch_board", blocks)).toBe(true);
  });
});

describe("canvasTools", () => {
  it("defaults to pen, highlighter, eraser when unset", () => {
    expect(canvasTools({})).toEqual(["pen", "highlighter", "eraser"]);
  });

  it("filters out an unknown or reserved tool (text is reserved: a stroke never carries text)", () => {
    expect(canvasTools({ tools: ["pen", "text", "sparkle", "box"] })).toEqual(["pen", "box"]);
  });

  it("de-duplicates repeated entries", () => {
    expect(canvasTools({ tools: ["pen", "pen", "arrow"] })).toEqual(["pen", "arrow"]);
  });
});

describe("canvasMaxStrokes", () => {
  it("defaults to 500", () => {
    expect(canvasMaxStrokes({})).toBe(500);
  });

  it("clamps to the contract's cap of 2,000", () => {
    expect(canvasMaxStrokes({ max_strokes: 50_000 })).toBe(2000);
  });

  it("falls back to the default for an invalid value", () => {
    expect(canvasMaxStrokes({ max_strokes: -5 })).toBe(500);
    expect(canvasMaxStrokes({ max_strokes: "lots" })).toBe(500);
  });
});

describe("canvasSignatureMode", () => {
  it("false unless the config sets it", () => {
    expect(canvasSignatureMode({})).toBe(false);
    expect(canvasSignatureMode({ signature_mode: true })).toBe(true);
  });
});
