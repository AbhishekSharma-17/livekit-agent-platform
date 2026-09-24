import * as React from "react";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { visibleSections } from "@/components/console/agents/editor/registry";
import { EDITOR_SECTIONS } from "@/components/console/agents/editor/sections";
import { FlowSection } from "@/components/console/flow/flow-section";
import { ModeSwitchChip } from "@/components/console/flow/mode-switch";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * The editor's Flow section is in the nav for prompt agents too: instead of
 * the canvas it explains flows and its "Switch to flow" button opens the
 * header chip's prompt ↔ flow dialog (one dialog, not a copy). Confirming
 * swaps the canvas in place.
 */

vi.mock("@/components/console/flow/flow-canvas", () => ({
  FlowCanvas: () => <p>Flow canvas</p>,
}));

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

const AGENT = {
  id: "a-1",
  slug: "a",
  name: "A",
  description: "",
  pack_id: "generic",
  ui_panel_id: "composite",
  published: false,
  config_version: 1,
  created_at: "2026-09-23T10:00:00Z",
  updated_at: "2026-09-23T10:00:00Z",
  mode: "prompt",
  config: { v: 2, instructions: "Be brief.", pipeline: { mode: "cascaded" }, flow: null },
} as AgentOut;

function Harness() {
  const form = useForm<AgentEditorForm>({ defaultValues: toFormValues(AGENT) });
  return (
    <FormProvider {...form}>
      <ModeSwitchChip agent={AGENT} />
      <FlowSection agent={AGENT} />
    </FormProvider>
  );
}

describe("Flow section visibility", () => {
  it.each(["prompt", "flow"] as const)("is in the editor nav in %s mode", (mode) => {
    const ids = visibleSections(EDITOR_SECTIONS, { agent: AGENT, mode }).map((section) => section.id);
    expect(ids).toContain("flow");
  });
});

describe("FlowSection in prompt mode", () => {
  it("explains flows and offers a Switch to flow button instead of the canvas", () => {
    render(<Harness />);
    expect(screen.getByRole("heading", { name: "This agent runs on a single prompt" })).toBeTruthy();
    for (const part of ["Nodes", "Transitions", "Variables"]) expect(screen.getByText(part)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Switch to flow" })).toBeTruthy();
    expect(screen.queryByText("Flow canvas")).toBeNull();
  });

  it("opens the header chip's switch dialog, and shows the canvas once confirmed", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to flow" }));

    const dialog = screen.getByRole("dialog", { name: "Switch to a flow?" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Use a flow" }));

    expect(await screen.findByText("Flow canvas")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Switch to flow" })).toBeNull();
    expect(screen.getByRole("button", { name: /Mode:/ }).textContent).toContain("Flow");
  });

  it("stays in prompt mode when the dialog is cancelled", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to flow" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Switch to flow" })).toBeTruthy();
  });
});
