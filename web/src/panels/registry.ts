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
 * 2. Add one import plus one entry to `PANELS` below, and one entry to
 *    `PANEL_META` in `components/shared/panel-meta.ts` (the console's copy;
 *    `tests/registry.test.tsx` keeps the two key sets equal). Nothing else in
 *    the session surface needs to change.
 *
 * A custom panel can render any platform block with `<Block>` from
 * `@/panels/blocks` (CONTRACTS-V2 §4.4 "`<Block>` for custom panels").
 */
import type { ReceivedMessage } from "@livekit/components-react";
import type { ComponentType } from "react";

import type {
  AgentAction,
  AgentPublicOut,
  UiRequest,
  UiRequestResult,
  UiState,
} from "@/contracts/lkap-contracts";
import type { PanelConnectionState } from "@/lib/livekit";
import { COMPOSITE_PANEL } from "@/panels/composite";
import { GENERIC_PANEL } from "@/panels/generic";
import { INSURANCE_NOTEBOOK_PANEL } from "@/panels/insurance_notebook";

/** A pack-defined intent (CONTRACTS §10 `AgentAction`, `Pack.on_ui_action`). */
export interface PanelUiAction {
  action: "ui_action";
  payload: { name: string; data?: unknown };
}

/**
 * A form block's answer (CONTRACTS-V2 §4.4, the V2-10 → V2-11 wire contract):
 * `{block_id, values}` on submit, `{block_id, cancelled: true}` on dismissal.
 */
export interface PanelFormSubmitAction {
  action: "form_submit";
  payload: { block_id: string; values: Record<string, unknown> } | { block_id: string; cancelled: true };
}

/**
 * A requestable block's answer (V5-02, CONTRACTS-V2 §4.4): `{block_id, values}`
 * on submit, `{block_id, cancelled: true}` on dismissal. Also answers a form.
 */
export interface PanelBlockSubmitAction {
  action: "block_submit";
  payload: { block_id: string; values: Record<string, unknown> } | { block_id: string; cancelled: true };
}

/** A block-level intent → the pack's `on_block_action(ctx, block_id, name, data)`. */
export interface PanelBlockAction {
  action: "block_action";
  payload: { block_id: string; name: string; data?: unknown };
}

/**
 * Every intent a panel may emit through `perform`. The session room sends
 * `ui_action` through `performUiAction` and the v2 block actions as their own
 * `lkap.agent.action` (`{v: 1, action, payload}`).
 */
export type PanelAction = PanelUiAction | PanelFormSubmitAction | PanelBlockSubmitAction | PanelBlockAction;

/**
 * The `lkap.agent.action` a panel intent becomes. `ui_action` keeps the v1
 * wire shape `useAgentRpc().performUiAction` sends (`{name, data}`, `data`
 * defaulting to `null`); the block actions pass their payload through.
 */
export function agentActionFor(action: PanelAction): AgentAction {
  if (action.action === "ui_action") {
    return { v: 1, action: "ui_action", payload: { name: action.payload.name, data: action.payload.data ?? null } };
  }
  return { v: 1, action: action.action, payload: { ...action.payload } };
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
  /**
   * Send an intent to the agent: a pack-defined `ui_action`
   * (`Pack.on_ui_action`) or a v2 block action (`form_submit`,
   * `block_action`). Resolves with the agent's `AgentActionResult`.
   */
  perform: (action: PanelAction) => Promise<unknown>;
  /** Live transcript from `useSessionMessages`, for panels that render turns. */
  transcript: ReceivedMessage[];
  connectionState: PanelConnectionState;
}

export interface PanelDefinition {
  /** Matches `PackManifest.ui_panel_id` / `PanelLayout.panel_id`. */
  id: string;
  title: string;
  Component: ComponentType<PanelProps>;
  /**
   * The layout this session asks for, when it depends on the agent (the
   * composite panel reads `PanelLayout.layout`). Wins over `layout`.
   */
  layoutFor?: (agent: AgentPublicOut) => "side" | "wide";
  /**
   * The panel renders `PanelLayout.blocks` (CONTRACTS-V2 §4.4). The composite
   * panel does; a custom panel that places `<Block>`s itself may opt in.
   */
  blocksAware?: boolean;
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
  [COMPOSITE_PANEL.id]: COMPOSITE_PANEL,
  [GENERIC_PANEL_ID]: GENERIC_PANEL,
  [INSURANCE_NOTEBOOK_PANEL.id]: INSURANCE_NOTEBOOK_PANEL,
};

/** The session layout for `panel` rendering `agent` (`layoutFor` → `layout` → side). */
export function panelLayoutFor(panel: PanelDefinition, agent: AgentPublicOut): "side" | "wide" {
  return panel.layoutFor?.(agent) ?? panel.layout ?? "side";
}

/** Look up a panel by id, falling back to the generic panel. */
export function resolvePanel(id: string | null | undefined): PanelDefinition {
  if (id) {
    const panel = PANELS[id];
    if (panel) return panel;
  }
  return PANELS[GENERIC_PANEL_ID];
}
