import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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

    const grid = shell?.querySelector(".grid");
    expect(grid?.className).toContain("lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]");
  });

  it("labels the panel column with the panel title", () => {
    renderShell("side");
    expect(screen.getByLabelText("Claim notebook")).toBeTruthy();
  });
});
