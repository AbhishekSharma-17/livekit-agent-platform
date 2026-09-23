/**
 * Static data for the preview route (docs/UI_UX_SPEC.md §7.11). Every scene
 * renders from these — no room, no API call, no `localStorage` — so the
 * capture script and `tests/preview-scenes.test.tsx` see the exact same
 * output as a browser hitting the route directly.
 *
 * Fixtures are imported from `web/tests/fixtures/*.json` (WP-9's golden
 * `UiState` set) rather than duplicated here, so a fixture update is picked
 * up by the preview route automatically.
 */
import type { AgentPublicOut, UiState } from "@/contracts/lkap-contracts";
import { normalizeUiState, type UiStateStore } from "@/lib/ui-state";

import genericUiState from "../../../tests/fixtures/generic_ui_state.json";
import notebookBlank from "../../../tests/fixtures/insurance_ui_state_lkap_blank.json";
import notebookAuto from "../../../tests/fixtures/insurance_ui_state_lkap_auto.json";
import notebookFlood from "../../../tests/fixtures/insurance_ui_state_lkap_flood.json";

/** The generic panel's reference agent (docs/UI_UX_SPEC.md §5.5). */
export const PREVIEW_GENERIC_AGENT: AgentPublicOut = {
  id: "preview-generic-agent",
  name: "Sam",
  slug: "preview-generic",
  description: "Books home-appliance service visits.",
  pipeline_mode: "cascaded",
  ui_panel_id: "generic",
  panel: { panel_id: "generic", layout: "side" },
  capabilities: {
    camera: true,
    screen_share: false,
    chat_input: true,
    vision_inject_per_turn: true,
    dtmf: false,
  },
};

/** The insurance notebook's reference agent — the flagship pack. */
export const PREVIEW_INSURANCE_AGENT: AgentPublicOut = {
  id: "preview-insurance-agent",
  name: "Maya",
  slug: "preview-insurance",
  description: "Takes first notice of loss for motor and home claims.",
  pipeline_mode: "cascaded",
  ui_panel_id: "insurance_notebook",
  panel: { panel_id: "insurance_notebook", layout: "wide" },
  capabilities: {
    camera: true,
    screen_share: false,
    chat_input: true,
    vision_inject_per_turn: true,
    dtmf: false,
  },
};

export type NotebookFixtureId = "blank" | "auto" | "flood";

const NOTEBOOK_FIXTURES: Record<NotebookFixtureId, UiState> = {
  blank: notebookBlank as UiState,
  auto: notebookAuto as UiState,
  flood: notebookFlood as UiState,
};

export const NOTEBOOK_FIXTURE_IDS = Object.keys(
  NOTEBOOK_FIXTURES,
) as NotebookFixtureId[];

/** `resolvePanel("insurance_notebook")`'s state for one of the three golden fixtures. */
export function notebookState(id: NotebookFixtureId): UiStateStore["state"] {
  return normalizeUiState(NOTEBOOK_FIXTURES[id]);
}

/** `resolvePanel("generic")`'s state — the single generic fixture. */
export function genericState(): UiStateStore["state"] {
  return normalizeUiState(genericUiState as UiState);
}

/** A no-op `perform` — panels are pure renderers; the preview never calls the agent. */
export async function previewPerform(): Promise<undefined> {
  return undefined;
}
