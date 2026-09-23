import * as React from "react";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";

import { buildAgentUpdate, toFormValues } from "@/components/console/agents/editor/form-values";
import { flowDraftIssues, hasErrors, normalizeFlow } from "@/components/console/flow/flow-model";
import { ModeSwitchChip } from "@/components/console/flow/mode-switch";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, AgentUpdate } from "@/contracts/lkap-contracts";

/**
 * V2-16: the header's prompt ↔ flow switch. The api derives `mode` from
 * `config.flow` (R-V2-12), so the switch changes both and the save payload
 * carries a flow with nodes (→ "flow") or `flow: null` (→ "prompt").
 */

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

let payload: AgentUpdate | null = null;

function Probe() {
  const { getValues } = useFormContext<AgentEditorForm>();
  return (
    <button type="button" onClick={() => (payload = buildAgentUpdate(AGENT, getValues()))}>
      Build payload
    </button>
  );
}

function Harness() {
  const form = useForm<AgentEditorForm>({ defaultValues: toFormValues(AGENT) });
  return (
    <FormProvider {...form}>
      <ModeSwitchChip agent={AGENT} />
      <Probe />
    </FormProvider>
  );
}

function switchMode(confirmLabel: string) {
  fireEvent.click(screen.getByRole("button", { name: /Mode:/ }));
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: confirmLabel }));
  fireEvent.click(screen.getByRole("button", { name: "Build payload" }));
}

describe("ModeSwitchChip", () => {
  it("switches to a seeded, valid flow and back to a prompt, after a confirmation", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: /Mode:/ }).textContent).toContain("Prompt");

    switchMode("Use a flow");
    expect(screen.getByRole("button", { name: /Mode:/ }).textContent).toContain("Flow");
    expect(payload?.mode).toBe("flow");
    const flow = normalizeFlow(payload?.config?.flow);
    expect(flow.nodes.map((node) => node.kind)).toEqual(["start", "agent"]);
    expect(hasErrors(flowDraftIssues(flow))).toBe(false);
    expect(payload?.config?.instructions).toBe("Be brief."); // R-V2-13: the base prompt stays

    switchMode("Use a prompt");
    expect(payload?.mode).toBeUndefined(); // unchanged from the stored "prompt"
    expect(payload?.config?.flow).toBeNull();
  });

  it("does nothing until confirmed", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Mode:/ }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Build payload" }));
    expect(payload?.mode).toBeUndefined();
    expect(payload?.config?.flow).toBeNull();
  });
});
