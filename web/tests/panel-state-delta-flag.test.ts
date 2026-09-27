import { describe, expect, it } from "vitest";

import type { AgentOut } from "@/contracts/lkap-contracts";
import { panelFormValue } from "@/components/console/agents/editor/form-values";
import { switchPanel } from "@/components/console/agents/panel-section/composer-model";
import { panelLayoutSchema } from "@/components/console/lib/schemas";

/**
 * V5-43, ruling on ask #309: `PanelLayout.accept_state_delta` (default off) has no
 * switch in the console yet (V5-44 adds it), but a save must never drop a stored `true`.
 */
function agentWith(panel: Record<string, unknown>): AgentOut {
  return { ui_panel_id: "composite", config: { panel } } as unknown as AgentOut;
}

describe("accept_state_delta is carried losslessly", () => {
  it("survives loading, validating and switching panels", () => {
    const form = panelFormValue(agentWith({ panel_id: "composite", layout: "side", blocks: [], accept_state_delta: true }));
    expect(form.accept_state_delta).toBe(true);
    expect(panelLayoutSchema.parse(form).accept_state_delta).toBe(true);
    expect(switchPanel(form, "insurance_notebook").accept_state_delta).toBe(true);
    expect(switchPanel(switchPanel(form, "insurance_notebook"), "composite").accept_state_delta).toBe(true);
  });

  it("stays absent when the stored panel has none", () => {
    const form = panelFormValue(agentWith({ panel_id: "composite", layout: "side", blocks: [] }));
    expect("accept_state_delta" in form).toBe(false);
  });
});
