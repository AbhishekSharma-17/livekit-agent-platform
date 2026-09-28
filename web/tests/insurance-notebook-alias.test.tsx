import * as React from "react";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut, UiState } from "@/contracts/lkap-contracts";
import { normalizeUiState } from "@/lib/ui-state";
import { blockDomId } from "@/panels/blocks";
import {
  INSURANCE_NOTEBOOK_ALIAS,
  INSURANCE_NOTEBOOK_ALIAS_ID,
  NOTEBOOK_PRESET_LAYOUT,
  PANELS,
  panelLayoutFor,
  resolvePanel,
  withNotebookPreset,
  type PanelProps,
} from "@/panels/registry";

import autoFixture from "./fixtures/insurance_ui_state_lkap_auto.json";

/**
 * V6-22 (D-V6-21): `insurance_notebook` is a legacy alias. The insurance pack's custom React
 * notebook is deleted; an agent saved with the pack's panel id renders the composite panel with
 * the Notebook preset's blocks, in the wide layout.
 */

beforeAll(() => {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterEach(cleanup);

/** An agent created from the legacy insurance pack (`pack_id="insurance_claim"`), as connect sends it. */
const LEGACY_AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "insurance-claim",
  name: "Demo — Claims assistant",
  description: "Takes first notice of loss.",
  ui_panel_id: "insurance_notebook",
  panel: { panel_id: "insurance_notebook", layout: "wide", blocks: [] },
  pipeline_mode: "cascaded",
  capabilities: { camera: true, screen_share: false, chat_input: true, vision_inject_per_turn: true, dtmf: false },
};

function props(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: normalizeUiState(autoFixture as unknown as UiState),
    assets: new Map(),
    agent: LEGACY_AGENT,
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

describe("insurance_notebook legacy alias", () => {
  it("resolves the legacy id to the alias, in the wide layout, with the composite request handler", () => {
    const panel = resolvePanel(INSURANCE_NOTEBOOK_ALIAS_ID);

    expect(panel).toBe(INSURANCE_NOTEBOOK_ALIAS);
    expect(PANELS.insurance_notebook).toBe(INSURANCE_NOTEBOOK_ALIAS);
    expect(panel.layout).toBe("wide");
    expect(panelLayoutFor(panel, { ...LEGACY_AGENT, panel: { panel_id: "insurance_notebook", layout: "side" } })).toBe(
      "wide",
    );
    expect(panel.handleRequest?.({ method: "open_dialog", payload: { dialog: "packet" } })).toMatchObject({ ok: false });
  });

  it("an agent with pack_id=insurance_claim and panel_id=insurance_notebook renders the Notebook preset", async () => {
    render(<INSURANCE_NOTEBOOK_ALIAS.Component {...props()} />);

    const composite = screen.getByTestId("composite-panel");
    expect(composite.getAttribute("data-layout")).toBe("wide");
    // The notebook renderer is loaded lazily; give it room under a full, parallel test run.
    await waitFor(() => expect(screen.getByTestId("block-notebook").getAttribute("data-loading")).toBeNull(), {
      timeout: 5000,
    });
    for (const id of ["status", "notebook", "gallery"]) {
      expect(document.getElementById(blockDomId(id)), id).not.toBeNull();
    }
    // The drawing board shows inside the notebook's Sketch section, not a second time beside it.
    expect(within(screen.getByTestId("block-notebook")).getAllByText("Sketch").length).toBeGreaterThan(0);
    const topLevel = [...composite.children].map((child) => child.querySelector("[data-block-id]")?.getAttribute("data-block-id"));
    expect(topLevel).not.toContain("sketch_board");
    // The pack still sets the envelope status stamp, and the preset's status block shows it.
    expect(screen.getByText("Escalate to human")).toBeTruthy();
  });

  it("uses the preset whatever blocks the connect response carries, without touching the agent", () => {
    const agent = { ...LEGACY_AGENT, panel: { ...LEGACY_AGENT.panel, blocks: [] } };

    const aliased = withNotebookPreset(agent);

    expect(aliased.panel.layout).toBe("wide");
    expect(aliased.panel.blocks?.map((block) => [block.id, block.type])).toEqual([
      ["status", "status"],
      ["notebook", "notebook"],
      ["sketch_board", "canvas"],
      ["gallery", "gallery"],
    ]);
    expect(agent.panel.blocks).toEqual([]);
    expect(aliased.panel.blocks?.[0]).not.toBe(NOTEBOOK_PRESET_LAYOUT.blocks?.[0]);
  });
});
