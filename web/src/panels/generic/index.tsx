"use client";

/**
 * Generic panel — the reference `PanelDefinition` (docs/CONTRACTS.md §11) and
 * the visual reference for the v2 panel blocks
 * (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §3).
 *
 * Renders every slot of the platform envelope (`status`, `progress`, `notes`,
 * `checklist`, `assets`, `activity`) plus a collapsible JSON view of the
 * pack-defined `custom` object, so any pack has a usable panel before it ships
 * a bespoke one. It is a pure renderer: the only way out is `perform`.
 *
 * The slot renderers live in `./blocks` so a pack panel or the v2 panel
 * composer can reuse exactly what this panel draws.
 */
import * as React from "react";
import { useMemo } from "react";

import type { PanelDefinition, PanelProps } from "@/panels/registry";

import {
  ActivityBlock,
  AssetsBlock,
  ChecklistBlock,
  NotesBlock,
  PanelBlock,
  StatusBlock,
} from "./blocks";

export function GenericPanel({ state, assets }: PanelProps) {
  const notes = state.notes ?? [];
  const checklist = state.checklist ?? [];
  const assetRefs = state.assets ?? [];
  const activity = state.activity ?? [];
  const custom = useMemo(() => state.custom ?? {}, [state.custom]);

  const customJson = useMemo(() => {
    try {
      return JSON.stringify(custom, null, 2);
    } catch {
      return "{}";
    }
  }, [custom]);

  return (
    <div data-testid="generic-panel" className="flex h-full flex-col overflow-y-auto">
      <StatusBlock status={state.status} progress={state.progress} />

      <PanelBlock title="Notes" count={notes.length}>
        <NotesBlock notes={notes} />
      </PanelBlock>

      <PanelBlock title="Still needed" count={checklist.length}>
        <ChecklistBlock items={checklist} />
      </PanelBlock>

      <PanelBlock title="Attachments" count={assetRefs.length}>
        <AssetsBlock assets={assetRefs} urls={assets} />
      </PanelBlock>

      <PanelBlock title="Activity" count={activity.length}>
        <ActivityBlock events={activity} />
      </PanelBlock>

      {Object.keys(custom).length > 0 && (
        <PanelBlock title="Pack data">
          <details className="group">
            <summary className="text-muted-foreground hover:text-foreground focus-visible:ring-ring cursor-pointer rounded-sm text-sm focus-visible:ring-2 focus-visible:outline-none">
              Show raw state
            </summary>
            <pre className="bg-muted/40 text-muted-foreground mt-2 max-h-72 overflow-auto rounded-md p-3 font-mono text-[0.8125rem] leading-relaxed">
              {customJson}
            </pre>
          </details>
        </PanelBlock>
      )}
    </div>
  );
}

export const GENERIC_PANEL: PanelDefinition = {
  id: "generic",
  title: "Session",
  Component: GenericPanel,
  layout: "side",
};
