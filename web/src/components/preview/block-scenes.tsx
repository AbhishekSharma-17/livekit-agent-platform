/**
 * V2-11's preview scenes: every panel block alone (`scene=blocks`) and the
 * composite panel with all twelve inside the real session shell
 * (`scene=composite`). Registered in `scenes.tsx`.
 *
 * `CompositePreview` is also the agent editor's live preview
 * (`components/console/agents/panel-section/composer-preview.tsx`): the
 * composer renders the author's *draft* layout through exactly this
 * component, so what the composer shows and what the capture script
 * screenshots are the same pixels. (The preview route itself cannot take a
 * free-form block list — scene params are enumerated — hence the shared
 * component rather than an iframe.)
 */
import * as React from "react";

import type { AgentPublicOut, BlockSpec, PanelLayout } from "@/contracts/lkap-contracts";
import { emptyUiState, normalizeUiState, type UiStateStore } from "@/lib/ui-state";
import { Block } from "@/panels/blocks";
import { BLOCK_CATALOG, BLOCK_TYPES } from "@/panels/blocks/catalog";
import {
  BLOCK_FIXTURE_STATES,
  FIXTURE_ASSET_REFS,
  FIXTURE_LAYOUT,
  FIXTURE_TRANSCRIPT,
  FORM_SUBMITTED_STATE,
  fixtureAssetUrls,
  fixtureUiState,
} from "@/panels/blocks/__fixtures__";
import { CompositePanel } from "@/panels/composite";
import {
  effectiveLayout,
  type BlockType,
} from "@/panels/composite/layout";

import { previewPerform } from "./fixtures";

const NO_ASSETS = new Map<string, string>();

/** The agent the block scenes render for. */
export function previewCompositeAgent(panel: PanelLayout): AgentPublicOut {
  return {
    id: "preview-composite-agent",
    name: "Maya",
    slug: "preview-composite",
    description: "Takes first notice of loss for home claims.",
    pipeline_mode: "cascaded",
    ui_panel_id: panel.panel_id ?? "composite",
    capabilities: { camera: true, screen_share: false, chat_input: true, vision_inject_per_turn: false },
    panel: panel as AgentPublicOut["panel"],
  };
}

/**
 * Fixture state for an arbitrary layout: every block gets its type's filled
 * fixture, keyed by the block's own id — so a draft with ids the fixtures
 * never heard of still renders filled blocks.
 */
export function fixtureStateFor(blocks: readonly BlockSpec[]): UiStateStore["state"] {
  const states: Record<string, unknown> = {};
  for (const spec of blocks) states[spec.id] = BLOCK_FIXTURE_STATES[spec.type] ?? {};
  return fixtureUiState(states);
}

/** The composite panel rendering `layout` against the fixtures (no room). */
export function CompositePreview({ layout, className }: { layout: PanelLayout; className?: string }) {
  const resolved = effectiveLayout(layout);
  const agent = React.useMemo(() => previewCompositeAgent(layout), [layout]);
  const state = React.useMemo(() => fixtureStateFor(resolved.blocks), [resolved.blocks]);
  const assets = React.useMemo(() => fixtureAssetUrls(), []);
  return (
    <div className={className}>
      <CompositePanel
        state={state}
        assets={assets}
        agent={agent}
        sessionId="preview-session"
        perform={previewPerform}
        transcript={FIXTURE_TRANSCRIPT}
        connectionState="connected"
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* scene=blocks — one block alone                                             */
/* -------------------------------------------------------------------------- */

export const BLOCK_SCENE_STATES = ["filled", "empty", "submitted"] as const;
export type BlockSceneState = (typeof BLOCK_SCENE_STATES)[number];

/** The single-block spec a `blocks` scene renders (title from the catalog). */
export function blockSceneSpec(type: BlockType): BlockSpec {
  const fromLayout = FIXTURE_LAYOUT.blocks.find((spec) => spec.type === type);
  return fromLayout ?? { id: type, type, title: BLOCK_CATALOG[type].defaultTitle, config: {}, order: 0 };
}

export function BlockScene({ type, state }: { type: BlockType; state: BlockSceneState }) {
  const spec = blockSceneSpec(type);
  const empty = state === "empty";
  const blockState =
    type === "form" && state === "submitted" ? FORM_SUBMITTED_STATE : empty ? undefined : BLOCK_FIXTURE_STATES[type];
  const ui = empty
    ? normalizeUiState({ ...emptyUiState(), blocks: {} })
    : fixtureUiState(blockState ? { [spec.id]: blockState } : {});
  const agent = previewCompositeAgent({ panel_id: "composite", layout: "side", blocks: [spec] });
  return (
    <div className="bg-stage flex min-h-dvh w-full justify-center p-4">
      <div
        data-testid="preview-block"
        data-block-type={type}
        className="border-border bg-card w-full max-w-md self-start overflow-hidden rounded-xl border"
      >
        <Block
          spec={spec}
          state={ui}
          assets={empty ? NO_ASSETS : fixtureAssetUrls()}
          agent={agent}
          sessionId="preview-session"
          perform={previewPerform}
          transcript={empty ? [] : FIXTURE_TRANSCRIPT}
          connectionState="connected"
        />
      </div>
    </div>
  );
}

/** `scene=blocks` combos: every type filled and empty, plus a submitted form. */
export function blockSceneCombos(): { params: Record<string, string>; name: string }[] {
  return [
    ...BLOCK_TYPES.map((type) => ({ params: { type, state: "filled" }, name: `${type}-filled` })),
    ...BLOCK_TYPES.map((type) => ({ params: { type, state: "empty" }, name: `${type}-empty` })),
    { params: { type: "form", state: "submitted" }, name: "form-submitted" },
  ];
}

export { BLOCK_TYPES, FIXTURE_ASSET_REFS, FIXTURE_LAYOUT };
