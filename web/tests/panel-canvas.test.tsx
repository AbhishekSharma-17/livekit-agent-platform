import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { RoomContext } from "@livekit/components-react";
import type { Room } from "livekit-client";

import type { AgentPublicOut, BlockSpec, CanvasBlockState, PanelLayout } from "@/contracts/lkap-contracts";
import { emptyUiState } from "@/lib/ui-state";
import { Block } from "@/panels/blocks";
import { BLOCK_FIXTURE_STATES, FIXTURE_LAYOUT, FIXTURE_TRANSCRIPT, fixtureAssetUrls, fixtureUiState } from "@/panels/blocks/__fixtures__";
import { handleCompositeRequest, subscribeCompositeRequests } from "@/panels/composite/requests";
import type { PanelProps } from "@/panels/registry";
import { TOPIC_UI_INK } from "@/lib/livekit";
import { TOPIC_UI_UPLOAD } from "@/panels/composite/upload";

/**
 * V6-14, ask #92: the drawing board — pointer input sending on `lkap.ui.ink`, permissions
 * derived from the whole panel (not received as a prop, so a notebook's ink section works
 * the same as a top-level board), the "board is full" state, and the snapshot answer on
 * the existing `lkap.ui.upload` path.
 *
 * `rasteriseBoard` is mocked: jsdom's `<canvas>` has no real 2D context, so a genuine
 * rasterisation always resolves `null` here — the mock stands in for "the board renders to
 * some PNG", letting this file assert the request/ack/upload wiring around it instead
 * (`canvas-rasterise.test.ts` covers the drawing calls themselves against a fake context).
 */
vi.mock("@/panels/blocks/canvas/rasterise", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/panels/blocks/canvas/rasterise")>();
  return { ...actual, rasteriseBoard: vi.fn(async () => new Blob(["fake-png"], { type: "image/png" })) };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function fakeRoom() {
  const sendText = vi.fn(async (_text: string, _options?: { topic?: string; compress?: boolean }) => ({}));
  const writer = { write: vi.fn(async (_chunk: Uint8Array) => {}), close: vi.fn(async () => {}) };
  const streamBytes = vi.fn(async (_options: Record<string, unknown>) => writer);
  const room = { localParticipant: { sendText, streamBytes } };
  return { room, sendText, streamBytes, writer };
}

function agent(panel?: PanelLayout): AgentPublicOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Maya",
    description: "",
    pipeline_mode: "cascaded",
    ui_panel_id: "composite",
    capabilities: {},
    panel: (panel ?? FIXTURE_LAYOUT) as AgentPublicOut["panel"],
  };
}

function panelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: fixtureUiState(),
    assets: fixtureAssetUrls(),
    agent: agent(),
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: FIXTURE_TRANSCRIPT,
    connectionState: "connected",
    ...overrides,
  };
}

const boardSpec = FIXTURE_LAYOUT.blocks.find((b) => b.type === "canvas") as BlockSpec;

/** A minimal panel with just this one block — so `canvasCallerCanDraw` reads *this* config, not `FIXTURE_LAYOUT`'s own "board" block. */
function singleBlockLayout(spec: BlockSpec): PanelLayout {
  return { panel_id: "composite", layout: "side", blocks: [spec] } as PanelLayout;
}

async function findBoardEl() {
  await waitFor(() => expect(screen.getByTestId("block-canvas").getAttribute("data-loading")).toBeNull());
  return screen.getByTestId("block-canvas");
}

/**
 * This jsdom has no `PointerEvent` constructor at all, and its `MouseEvent`/`UIEvent`
 * never reads `clientX`/`clientY` off the init dict either (`fireEvent.pointerDown(el,
 * {clientX, clientY})` dispatches an event whose `clientX`/`clientY` come back
 * `undefined`). React's event system only cares that the dispatched event's `type`
 * matches ("pointerdown"/"pointermove"/"pointerup") and reads whatever properties the
 * native event object carries — so a plain `MouseEvent` of that type, with the
 * pointer-specific fields (`pointerId`, `pointerType`, `pressure`) and `clientX`/`clientY`
 * forced on afterwards, is indistinguishable to `onPointerDown` et al.
 */
function firePointer(
  el: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  init: { pointerId: number; clientX: number; clientY: number; pointerType?: string; pressure?: number; buttons?: number },
): void {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "pointerId", { value: init.pointerId, configurable: true });
  Object.defineProperty(event, "pointerType", { value: init.pointerType ?? "mouse", configurable: true });
  Object.defineProperty(event, "pressure", { value: init.pressure ?? 0, configurable: true });
  Object.defineProperty(event, "buttons", { value: init.buttons ?? (type === "pointerup" ? 0 : 1), configurable: true });
  Object.defineProperty(event, "clientX", { value: init.clientX, configurable: true });
  Object.defineProperty(event, "clientY", { value: init.clientY, configurable: true });
  el.dispatchEvent(event);
}

function findSvg(el: HTMLElement): SVGSVGElement {
  const svg = el.querySelector('[data-slot="canvas-board"]');
  if (!svg) throw new Error("no canvas-board svg");
  // jsdom never lays elements out; the board reads this to normalise pointer coordinates.
  // A plain assignment doesn't reliably shadow an SVG element's inherited method under
  // jsdom's WebIDL bindings — `defineProperty` does.
  Object.defineProperty(svg, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ left: 0, top: 0, width: 200, height: 150, right: 200, bottom: 150, x: 0, y: 0, toJSON: () => ({}) }),
  });
  return svg as SVGSVGElement;
}

describe("canvas block — read-only (no room)", () => {
  it("renders the fixture's strokes and shapes, with no toolbar and no drawing sent", async () => {
    render(<Block spec={boardSpec} {...panelProps()} />);
    const el = await findBoardEl();
    expect(within(el).queryByRole("toolbar")).toBeNull();
    expect(el.querySelectorAll('[data-slot="canvas-stroke"]').length).toBe((BLOCK_FIXTURE_STATES.canvas as CanvasBlockState).strokes?.length ?? 0);
    expect(within(el).getByText("Drawing is available during a call.")).toBeTruthy();
  });

  it("shows nothing drawn without complaint on an agent-only board (caller_can_draw off)", async () => {
    const spec: BlockSpec = { id: "board", type: "canvas", config: { caller_can_draw: false } };
    render(
      <Block
        spec={spec}
        {...panelProps({ agent: agent(singleBlockLayout(spec)), state: emptyUiState(), assets: new Map(), transcript: [] })}
      />,
    );
    const el = await findBoardEl();
    expect(within(el).queryByText("Drawing is available during a call.")).toBeNull();
    expect(within(el).queryByRole("toolbar")).toBeNull();
  });
});

describe("canvas block — permissions from the whole panel (ask #94-style wiring)", () => {
  it("a board a notebook's ink section claims draws when the notebook allows it, even though the board's own flag is off", async () => {
    const notebookSpec: BlockSpec = {
      id: "nb",
      type: "notebook",
      title: "Notebook",
      config: {
        sections: [{ id: "sketch", kind: "ink", canvas_block_id: "board" }],
        caller_can_draw: true,
      },
    };
    const canvasSpec: BlockSpec = { id: "board", type: "canvas", title: "Sketch", config: { caller_can_draw: false } };
    const layout: PanelLayout = { panel_id: "composite", layout: "wide", blocks: [notebookSpec, canvasSpec] } as PanelLayout;
    const { room } = fakeRoom();
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={canvasSpec} {...panelProps({ agent: agent(layout), state: emptyUiState() })} />
      </RoomContext.Provider>,
    );
    const el = await findBoardEl();
    expect(within(el).getByRole("toolbar")).toBeTruthy();
  });
});

describe("canvas block — pointer input sends on lkap.ui.ink", () => {
  it("a pen drag sends a stroke starting with the pointer-down point, then ends it", async () => {
    const { room, sendText } = fakeRoom();
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={boardSpec} {...panelProps({ state: emptyUiState() })} />
      </RoomContext.Provider>,
    );
    const el = await findBoardEl();
    const svg = findSvg(el);

    await act(async () => {
      firePointer(svg, "pointerdown", { pointerId: 1, clientX: 20, clientY: 15 });
      firePointer(svg, "pointermove", { pointerId: 1, clientX: 100, clientY: 75 });
      firePointer(svg, "pointerup", { pointerId: 1, clientX: 100, clientY: 75 });
    });

    await waitFor(() => expect(sendText).toHaveBeenCalled());
    const [firstJson, firstOptions] = sendText.mock.calls[0];
    expect(firstOptions).toEqual({ topic: TOPIC_UI_INK, compress: false });
    const first = JSON.parse(firstJson);
    expect(first).toMatchObject({ block_id: "board", op: "add", tool: "pen" });
    expect(first.points[0]).toEqual([0.1, 0.1]); // (20,15) of a 200x150 rect -> (0.1, 0.1)
  });

  it("does not draw when the caller may not — no sender call at all", async () => {
    const { room, sendText } = fakeRoom();
    const spec: BlockSpec = { id: "board", type: "canvas", config: { caller_can_draw: false } };
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={spec} {...panelProps({ agent: agent(singleBlockLayout(spec)), state: emptyUiState() })} />
      </RoomContext.Provider>,
    );
    const el = await findBoardEl();
    const svg = findSvg(el);
    await act(async () => {
      firePointer(svg, "pointerdown", { pointerId: 1, clientX: 20, clientY: 15 });
      firePointer(svg, "pointerup", { pointerId: 1, clientX: 20, clientY: 15 });
    });
    expect(sendText).not.toHaveBeenCalled();
  });
});

describe("canvas block — the board is full", () => {
  it("shows the full notice and a live region, from limit_reached in state", async () => {
    const { room } = fakeRoom();
    const full = fixtureUiState({ board: { ...BLOCK_FIXTURE_STATES.canvas, limit_reached: true } });
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={boardSpec} {...panelProps({ state: full })} />
      </RoomContext.Provider>,
    );
    const el = await findBoardEl();
    const notice = el.querySelector('[data-slot="canvas-full-notice"]');
    expect(notice?.textContent).toBe("The board is full. Clear it to keep drawing.");
    const live = el.querySelector('[data-slot="canvas-live-region"]');
    expect(live?.textContent).toContain("The board is full");
  });
});

describe("canvas block — the snapshot answer (read_canvas, ask #92)", () => {
  it("acks {ok: true} at once, then streams a PNG on lkap.ui.upload named after the block", async () => {
    const { room, streamBytes, writer } = fakeRoom();
    render(
      <RoomContext.Provider value={room as unknown as Room}>
        <Block spec={boardSpec} {...panelProps()} />
      </RoomContext.Provider>,
    );
    await findBoardEl();

    let result: ReturnType<typeof handleCompositeRequest> | undefined;
    await act(async () => {
      result = handleCompositeRequest({ method: "snapshot", payload: { block_id: "board" } });
    });
    expect(result).toEqual({ ok: true, payload: {} });

    await waitFor(() => expect(streamBytes).toHaveBeenCalled());
    expect(streamBytes).toHaveBeenCalledWith(
      expect.objectContaining({
        topic: TOPIC_UI_UPLOAD,
        name: "board.png",
        mimeType: "image/png",
        attributes: { block_id: "board", name: "board.png" },
      }),
    );
    expect(writer.close).toHaveBeenCalled();
  });

  it("answers ok:false for a block id with no mounted board", () => {
    const result = handleCompositeRequest({ method: "snapshot", payload: { block_id: "not-a-board" } });
    expect(result.ok).toBe(false);
  });

  it("answers ok:false without a block_id", () => {
    const result = handleCompositeRequest({ method: "snapshot", payload: {} });
    expect(result.ok).toBe(false);
  });
});

describe("subscribeCompositeRequests — snapshot event shape", () => {
  it("delivers a snapshot event to a subscriber and respects its handled/not-handled answer", () => {
    const seen: unknown[] = [];
    const unsubscribe = subscribeCompositeRequests((event) => {
      seen.push(event);
      return event.kind === "snapshot" && event.blockId === "mine";
    });
    const handled = handleCompositeRequest({ method: "snapshot", payload: { block_id: "mine" } });
    unsubscribe();
    expect(seen).toEqual([{ kind: "snapshot", blockId: "mine" }]);
    expect(handled).toEqual({ ok: true, payload: {} });
  });
});
