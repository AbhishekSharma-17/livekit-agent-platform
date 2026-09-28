"use client";

/**
 * `layout` block (V6-08 → V6-10, D-V6-18): resolves `config.children[].block_id`
 * against the panel's own blocks (`panelLayoutOf(panel.agent)`) and renders
 * each one with `<Block>` — recursively, but never a nested `layout` (a layout
 * cannot hold another layout, `layout_issues`) and never a child that is no
 * longer on the panel (a stale session after a save removed it) — plus the
 * two presentational shells, `LayoutTabs` (keyboard-navigable tabs) and
 * `LayoutColumns` (2–3 columns, stacking to one at 375 px).
 *
 * Loaded lazily by `<Block>` (`blocks/index.tsx`): `radix-ui`'s Tabs
 * primitive stays out of the first load for a panel with no `layout` block.
 * `LayoutBlock` imports `Block` back from `./index` — safe despite the
 * two-way reference, because `index.tsx` only ever reaches this module
 * through a dynamic `import()` (`React.lazy`), by which point `index.tsx`
 * itself has already finished evaluating and `Block` exists.
 */
import * as React from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useBlockHighlight } from "@/panels/composite/block-highlight-context";
import { panelLayoutOf } from "@/panels/composite/layout";
import { PanelEmpty } from "@/panels/generic/blocks";

import { Block } from "./index";
import { blockTitle } from "./catalog";
import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export interface LayoutItem {
  id: string;
  label: string;
  node: React.ReactNode;
}

/** `kind: "tabs"` — the first item's tab is active by default. */
export function LayoutTabs({ items }: { items: LayoutItem[] }) {
  const [value, setValue] = React.useState(items[0]?.id);
  // A config change (a child added/removed) that drops the active tab falls back to the first.
  const active = items.some((item) => item.id === value) ? value : items[0]?.id;
  return (
    <Tabs value={active} onValueChange={setValue} data-slot="layout-tabs">
      <TabsList className="w-full justify-start overflow-x-auto">
        {items.map((item) => (
          <TabsTrigger key={item.id} value={item.id} className="shrink-0">
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {items.map((item) => (
        <TabsContent key={item.id} value={item.id} className="mt-3">
          {item.node}
        </TabsContent>
      ))}
    </Tabs>
  );
}

const COLUMN_CLASS: Record<2 | 3, string> = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
};

/** `kind: "columns"` — side by side from `sm` up; one column (stacked) below 375 px. */
export function LayoutColumns({ items, columns }: { items: LayoutItem[]; columns: 2 | 3 }) {
  return (
    <div data-slot="layout-columns" className={cn("grid grid-cols-1 gap-4", COLUMN_CLASS[columns])}>
      {items.map((item) => (
        <div key={item.id} className="border-border min-w-0 rounded-md border" data-slot="layout-column">
          <p className="border-border text-muted-foreground border-b px-3 py-1.5 text-xs font-medium">{item.label}</p>
          {item.node}
        </div>
      ))}
    </div>
  );
}

export function LayoutBlock({ spec, panel, title, highlighted }: BlockRenderProps<Record<string, never>>) {
  const config = spec.config as { kind?: unknown; children?: { block_id?: unknown; label?: unknown }[]; columns?: unknown } | null;
  const kind = config?.kind === "columns" ? "columns" : "tabs";
  const columns = config?.columns === 3 ? 3 : 2;
  const highlightedId = useBlockHighlight();
  const specsById = React.useMemo(
    () => new Map(panelLayoutOf(panel.agent).blocks.map((childSpec) => [childSpec.id, childSpec])),
    [panel.agent],
  );
  const items: LayoutItem[] = (config?.children ?? [])
    .map((child): LayoutItem | null => {
      const blockId = typeof child.block_id === "string" ? child.block_id : null;
      const childSpec = blockId ? specsById.get(blockId) : undefined;
      if (!blockId || !childSpec || childSpec.type === "layout") return null;
      const label = (typeof child.label === "string" && child.label.trim()) || blockTitle(childSpec) || blockId;
      return { id: blockId, label, node: <Block key={blockId} spec={childSpec} {...panel} highlighted={highlightedId === blockId} /> };
    })
    .filter((item): item is LayoutItem => item !== null);

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {items.length === 0 ? (
        <PanelEmpty>This holds no blocks yet.</PanelEmpty>
      ) : kind === "tabs" ? (
        <LayoutTabs items={items} />
      ) : (
        <LayoutColumns items={items} columns={columns} />
      )}
    </BlockFrame>
  );
}

export default LayoutBlock;
