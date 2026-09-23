/**
 * Frontend panel registry — docs/CONTRACTS.md §11.
 *
 * A pack declares `PackManifest.ui_panel_id`; the connect response carries it
 * as `uiPanelId`, and the session page renders `resolvePanel(uiPanelId)`.
 *
 * Panels are **pure renderers** of the `UiState` envelope (+ asset object URLs)
 * and emit intents only through `perform` — never by calling the API or the
 * room directly.
 *
 * ### Adding a panel
 *
 * 1. Create `src/panels/<panel_id>/index.tsx` exporting a `PanelDefinition`
 *    (see `src/panels/generic/index.tsx` for the reference implementation).
 * 2. Add one import plus one entry to `PANELS` below. Nothing else in the
 *    session surface needs to change.
 */
import type { ReceivedMessage } from "@livekit/components-react";
import type { ComponentType } from "react";

import type {
  AgentPublicOut,
  UiRequest,
  UiRequestResult,
  UiState,
} from "@/contracts/lkap-contracts";
import type { PanelConnectionState } from "@/lib/livekit";
import { GENERIC_PANEL } from "@/panels/generic";
import { INSURANCE_NOTEBOOK_PANEL } from "@/panels/insurance_notebook";

/** The only intent shape a panel may emit (CONTRACTS §10 `AgentAction`). */
export interface PanelUiAction {
  action: "ui_action";
  payload: { name: string; data?: unknown };
}

export interface PanelProps {
  /**
   * The platform envelope. `state.custom` is pack-defined and validated
   * against `PackManifest.state_schema`; a panel narrows it itself (e.g. with
   * a zod schema derived from that JSON Schema).
   */
  state: UiState;
  /** `asset_id` → object URL for bytes delivered on `lkap.ui.asset`. */
  assets: Map<string, string>;
  agent: AgentPublicOut;
  sessionId: string;
  /** Send a pack-defined `ui_action` to the agent (`Pack.on_ui_action`). */
  perform: (action: PanelUiAction) => Promise<unknown>;
  /** Live transcript from `useSessionMessages`, for panels that render turns. */
  transcript: ReceivedMessage[];
  connectionState: PanelConnectionState;
}

export interface PanelDefinition {
  /** Matches `PackManifest.ui_panel_id`. */
  id: string;
  title: string;
  Component: ComponentType<PanelProps>;
  /**
   * `"side"` (default) docks the panel beside the stage; `"wide"` gives it the
   * main column and demotes the stage to a rail (the insurance notebook).
   */
  layout?: "side" | "wide";
  /**
   * Optional handler for the `lkap.ui.request` methods that target a panel
   * affordance (UI_UX_SPEC §5.4). The session room forwards a request here
   * when the rendered panel defines it, and declines politely (`{ok: false}`)
   * when it does not.
   *
   * Payload keys are fixed by CONTRACTS-V2 §4.4 (ruling R-V2-3b):
   * `open_dialog {dialog, params?}`, `focus {target}`,
   * `request_video_source {source}`, `toast {message, tone?}` — the last two
   * are handled by the room, not by a panel. The insurance notebook answers
   * `open_dialog {dialog: "packet"}`.
   */
  handleRequest?: (request: UiRequest) => UiRequestResult | Promise<UiRequestResult>;
}

export const GENERIC_PANEL_ID = "generic";

export const PANELS: Record<string, PanelDefinition> = {
  [GENERIC_PANEL_ID]: GENERIC_PANEL,
  [INSURANCE_NOTEBOOK_PANEL.id]: INSURANCE_NOTEBOOK_PANEL,
};

/** Look up a panel by id, falling back to the generic panel. */
export function resolvePanel(id: string | null | undefined): PanelDefinition {
  if (id) {
    const panel = PANELS[id];
    if (panel) return panel;
  }
  return PANELS[GENERIC_PANEL_ID];
}
