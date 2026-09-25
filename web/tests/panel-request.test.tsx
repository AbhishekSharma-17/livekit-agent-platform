import * as React from "react";

import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut, UiRequest } from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { FIXTURE_LAYOUT, fixtureAssetUrls, fixtureUiState } from "@/panels/blocks/__fixtures__";
import { CompositePanel } from "@/panels/composite";
import { handleCompositeRequest } from "@/panels/composite/requests";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import type { PanelProps } from "@/panels/registry";

/**
 * V5-03: the generic `request`/`block_submit` round trip — CONTRACTS-V2 §4.4,
 * the PLAN-V5 V5-03 card's acceptance bullets.
 *
 * `tests/panel-blocks.test.tsx` covers the form block's own rendering
 * (fields, coercion, the submitted/cancelled views); this file covers the
 * protocol pieces V5-03 adds: `composite/requests.ts`'s `request` method and
 * the `useBlockRequest` hook (`composite/use-block-request.ts`) directly.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function agent(): AgentPublicOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Maya",
    description: "",
    pipeline_mode: "cascaded",
    ui_panel_id: "composite",
    capabilities: {},
    panel: FIXTURE_LAYOUT,
  };
}

function panelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: fixtureUiState(),
    assets: fixtureAssetUrls(),
    agent: agent(),
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

function specOf(id: string) {
  const spec = FIXTURE_LAYOUT.blocks.find((b) => b.id === id);
  if (!spec) throw new Error(id);
  return spec;
}

function req(method: UiRequest["method"], payload: Record<string, unknown>): UiRequest {
  return { v: 1, method, payload };
}

// jsdom has no layout engine; the composite panel's reveal calls this to scroll a block into view.
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? ((() => {}) as typeof Element.prototype.scrollIntoView);

describe("composite requests: the `request` method (V5-02 → V5-03)", () => {
  it("acks `request` at once and reveals the block — identically to the legacy `form`", async () => {
    render(<CompositePanel {...panelProps()} />);
    expect(handleCompositeRequest(req("request", { block_id: "intake", timeout_s: 120 }))).toEqual({
      ok: true,
      payload: {},
    });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Full name")));
    expect(screen.getByTestId("block-form").getAttribute("data-highlighted")).toBe("true");
  });

  it("`request` and `form` produce the same ack shape, with or without a mounted panel", () => {
    expect(handleCompositeRequest(req("request", { block_id: "intake", timeout_s: 30 }))).toEqual({
      ok: true,
      payload: {},
    });
    expect(handleCompositeRequest(req("form", { block_id: "intake", schema: {}, prefill: {} }))).toEqual({
      ok: true,
      payload: {},
    });
  });

  it("refuses a `request` with no `block_id`, naming the method in the error", () => {
    const result = handleCompositeRequest(req("request", { timeout_s: 30 }));
    expect(result.ok).toBe(false);
    expect(result.payload?.error).toMatch(/request needs a `block_id`/);
  });
});

describe("useBlockRequest", () => {
  it("pending mirrors status === \"requested\", nothing else", () => {
    const perform: PanelProps["perform"] = vi.fn(async () => ({ ok: true }));
    const { result, rerender } = renderHook(
      ({ status }: { status: "idle" | "requested" | "submitted" | "cancelled" }) =>
        useBlockRequest("intake", status, perform),
      { initialProps: { status: "idle" } },
    );
    expect(result.current.pending).toBe(false);
    rerender({ status: "requested" });
    expect(result.current.pending).toBe(true);
    rerender({ status: "submitted" });
    expect(result.current.pending).toBe(false);
  });

  it("submit sends block_submit {block_id, values}", async () => {
    const perform: PanelProps["perform"] = vi.fn(async () => ({ ok: true, payload: {} }));
    const { result } = renderHook(() => useBlockRequest("intake", "requested", perform));
    await act(async () => {
      await result.current.submit({ full_name: "Priya" });
    });
    expect(perform).toHaveBeenCalledWith({
      action: "block_submit",
      payload: { block_id: "intake", values: { full_name: "Priya" } },
    });
    expect(result.current.error).toBeNull();
  });

  it("cancel sends block_submit {block_id, cancelled: true}", async () => {
    const perform: PanelProps["perform"] = vi.fn(async () => ({ ok: true, payload: {} }));
    const { result } = renderHook(() => useBlockRequest("intake", "requested", perform));
    await act(async () => {
      await result.current.cancel();
    });
    expect(perform).toHaveBeenCalledWith({
      action: "block_submit",
      payload: { block_id: "intake", cancelled: true },
    });
  });

  it("surfaces the agent's refusal and clears `sending` so the caller can retry at once", async () => {
    const perform: PanelProps["perform"] = vi.fn(async () => ({ ok: false, error: "values must be an object" }));
    const { result } = renderHook(() => useBlockRequest("intake", "requested", perform));
    await act(async () => {
      await result.current.submit({});
    });
    expect(result.current.error).toBe("values must be an object");
    expect(result.current.sending).toBeNull();
  });

  it("surfaces a transport failure the same way", async () => {
    const perform: PanelProps["perform"] = vi.fn(async () => {
      throw new Error("socket closed");
    });
    const { result } = renderHook(() => useBlockRequest("intake", "requested", perform));
    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.error).toBe("socket closed");
    expect(result.current.sending).toBeNull();
  });

  it("resets `sending`/`error` when `status` crosses out of \"requested\" (the agent accepted the answer)", async () => {
    const perform: PanelProps["perform"] = vi.fn(async () => ({ ok: false, error: "nope" }));
    const { result, rerender } = renderHook(
      ({ status }: { status: "idle" | "requested" | "submitted" | "cancelled" }) =>
        useBlockRequest("intake", status, perform),
      { initialProps: { status: "requested" } },
    );
    await act(async () => {
      await result.current.submit({ a: 1 });
    });
    expect(result.current.error).toBe("nope");
    rerender({ status: "submitted" });
    expect(result.current.error).toBeNull();
    expect(result.current.sending).toBeNull();
  });
});

describe("end to end: request → pending form → block_submit (the card's acceptance bullets)", () => {
  it("a `request` for a form block renders the pending form, and submit posts block_submit {values}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={specOf("intake")} {...panelProps({ perform })} />);

    // The RPC only reveals; the pending form is already on screen from state.
    expect(handleCompositeRequest(req("request", { block_id: "intake", timeout_s: 120 }))).toEqual({
      ok: true,
      payload: {},
    });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "priya@example.com" } });
    fireEvent.change(screen.getByLabelText("Date of loss"), { target: { value: "2026-09-21" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(perform).toHaveBeenCalledWith({
      action: "block_submit",
      payload: { block_id: "intake", values: expect.objectContaining({ full_name: "Priya Raman" }) },
    });
  });

  it("cancel posts block_submit {block_id, cancelled: true}, arriving through a `request` just like a `form`", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={specOf("intake")} {...panelProps({ perform })} />);
    expect(handleCompositeRequest(req("request", { block_id: "intake", timeout_s: 120 }))).toEqual({
      ok: true,
      payload: {},
    });
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({ action: "block_submit", payload: { block_id: "intake", cancelled: true } }),
    );
  });

  it("a reconnect snapshot at status=requested renders the pending form with no request/form RPC at all", () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={specOf("intake")} {...panelProps({ perform })} />);
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(perform).not.toHaveBeenCalled();
  });

  it("the legacy `form` method behaves identically to `request`", async () => {
    render(<CompositePanel {...panelProps()} />);
    expect(handleCompositeRequest(req("form", { block_id: "intake", schema: {}, prefill: {} }))).toEqual({
      ok: true,
      payload: {},
    });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Full name")));
  });
});
