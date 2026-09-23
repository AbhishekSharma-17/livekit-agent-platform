import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { sessionLayoutModel } from "@/components/session/session-layout";
import { SessionShell } from "@/components/session/session-shell";
import {
  GENERIC_PANEL_ID,
  PANELS,
  resolvePanel,
  type PanelDefinition,
  type PanelProps,
} from "@/panels/registry";

afterEach(cleanup);

/** Stand-in for the panel W2-WEB-NOTEBOOK will register. */
const WIDE_PANEL: PanelDefinition = {
  id: "insurance_notebook",
  title: "Claim notebook",
  layout: "wide",
  Component: function StubPanel({ state }: PanelProps) {
    return <div>stub panel {state.progress ?? 0}</div>;
  },
};

describe("panel registry", () => {
  it("ships the generic panel as the reference implementation", () => {
    const generic = PANELS[GENERIC_PANEL_ID];
    expect(generic).toBeDefined();
    expect(generic.id).toBe(GENERIC_PANEL_ID);
    expect(generic.layout).toBe("side");
    expect(typeof generic.Component).toBe("function");
  });

  it("every registered panel is keyed by its own id", () => {
    for (const [key, panel] of Object.entries(PANELS)) {
      expect(panel.id).toBe(key);
      expect(panel.title.length).toBeGreaterThan(0);
    }
  });

  it("carries the optional ui.request handler per panel", () => {
    // UI_UX_SPEC §5.4 / CONTRACTS-V2 §4.4: the notebook answers `open_dialog`;
    // the generic panel has no panel-specific affordance, so the room declines.
    expect(PANELS[GENERIC_PANEL_ID].handleRequest).toBeUndefined();
    expect(typeof PANELS["insurance_notebook"].handleRequest).toBe("function");

    const handled: PanelDefinition = {
      ...PANELS[GENERIC_PANEL_ID],
      handleRequest: (request) => ({ ok: request.method === "open_dialog" }),
    };
    expect(handled.handleRequest?.({ method: "open_dialog" })).toEqual({ ok: true });
    expect(handled.handleRequest?.({ method: "focus" })).toEqual({ ok: false });
  });

  it("resolves a known panel id", () => {
    expect(resolvePanel(GENERIC_PANEL_ID).id).toBe(GENERIC_PANEL_ID);
  });

  it("falls back to generic for unknown, empty and missing ids", () => {
    expect(resolvePanel("no_such_panel").id).toBe(GENERIC_PANEL_ID);
    expect(resolvePanel("").id).toBe(GENERIC_PANEL_ID);
    expect(resolvePanel(null).id).toBe(GENERIC_PANEL_ID);
    expect(resolvePanel(undefined).id).toBe(GENERIC_PANEL_ID);
  });
});

describe("SessionShell layout seam", () => {
  function renderShell(layout: NonNullable<PanelDefinition["layout"]>) {
    return render(
      <SessionShell
        layout={layout}
        panelTitle="Claim notebook"
        agentName="Claims assistant"
        agentState="listening"
        stage={<div>stage</div>}
        transcript={<div>transcript</div>}
        panel={<div>panel body</div>}
        controls={<div>controls</div>}
      />,
    );
  }

  it('renders the "side" layout used by the generic panel', () => {
    const { container } = renderShell("side");
    const shell = container.querySelector('[data-testid="session-shell"]');
    expect(shell?.getAttribute("data-layout")).toBe("side");
    expect(screen.getByText("panel body")).toBeTruthy();
    expect(screen.getByText("stage")).toBeTruthy();
  });

  it('gives the main column to a "wide" panel', () => {
    const { container } = renderShell(WIDE_PANEL.layout ?? "side");
    const shell = container.querySelector('[data-testid="session-shell"]');
    expect(shell?.getAttribute("data-layout")).toBe("wide");
    expect(screen.getByText("panel body")).toBeTruthy();

    // The column widths are WP-8's to choose; the seam is that the two
    // layouts differ and that `wide` demotes the stage to a rail.
    expect(sessionLayoutModel("wide").grid).not.toBe(sessionLayoutModel("side").grid);
  });

  it("labels the panel column with the panel title", () => {
    renderShell("side");
    expect(screen.getByLabelText("Claim notebook")).toBeTruthy();
  });
});
