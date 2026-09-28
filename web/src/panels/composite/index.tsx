"use client";

/**
 * The composite panel (CONTRACTS-V2 §4.4, registry id `composite`): renders
 * the session's `PanelLayout.blocks` in order with `<Block>`.
 *
 * - **Layout** comes from `ConnectResponse.agent.panel` (R-V2-7) through
 *   `panelLayoutOf` (`./layout.ts`, which also holds the one contract
 *   adapter). `side` is a single column; `wide` flows the blocks into two
 *   columns from `xl` up, with the big blocks (document, table, transcript,
 *   video, form) spanning both.
 * - **State** is the envelope from `useUiState`: block state under
 *   `state.blocks[<id>]`, envelope blocks read `state.status` etc.
 * - **Requests** (`request`, `form`, `show_block`, `focus`, `navigate`) arrive through
 *   `handleRequest` → `./requests.ts` → the mounted component, which scrolls
 *   a block into view (and focuses a form's first field) or asks the visitor
 *   before opening a link.
 * - **`layout` children** (V6-10, D-V6-18) are left out of this flat flow —
 *   `claimedChildIds` collects every id any `layout` block on the panel
 *   claims, and they render only inside that `layout` (`blocks/index.tsx`'s
 *   `LayoutBlock`, which resolves and renders them itself). The `show_block`
 *   highlight still needs to reach a claimed child, so it travels down
 *   through `BlockHighlightProvider` rather than the top-level `highlighted`
 *   prop those children never receive. A `canvas` a `notebook` `ink` section
 *   claims (`canvas_block_id`, V6-12, ask #93) is claimed the same way —
 *   `blocks/notebook/sections.tsx`'s `InkSectionView` renders it inside the
 *   section, not here.
 */
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { BlockSpec } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { Block, blockDomId } from "@/panels/blocks";
import { PanelEmpty } from "@/panels/generic/blocks";
import type { PanelDefinition, PanelProps } from "@/panels/registry";

import { bannerTextOf, ConsentBanner } from "./banner";
import { BlockHighlightProvider } from "./block-highlight-context";
import type { BlockType } from "./layout";
import { COMPOSITE_PANEL_ID, panelLayoutOf } from "./layout";
import { handleCompositeRequest, subscribeCompositeRequests, type CompositeRequestEvent } from "./requests";

/** Blocks that take the full width of a `wide` layout. */
const WIDE_SPAN: ReadonlySet<BlockType> = new Set<BlockType>(["status", "document", "table", "transcript", "video", "form", "layout", "canvas"]);

/**
 * Every block id any `layout` block claims as a child (V6-10, D-V6-18), plus
 * every `canvas` a `notebook`'s `ink` section claims (`canvas_block_id`,
 * V6-12, ask #93) — shown only inside that layout/section, never in the flat
 * flow too. Over-inclusive on purpose: it trusts the raw config rather than
 * replaying the api's full `layout_issues`/`canvas_claim_issues` validators
 * (a child or board claimed twice, say), so a block is still hidden from the
 * top level even under a config the api would flag — "shown in one place"
 * matters more here than "shown correctly".
 */
function claimedChildIds(blocks: readonly BlockSpec[]): ReadonlySet<string> {
  const claimed = new Set<string>();
  for (const spec of blocks) {
    if (spec.type === "layout") {
      const children = (spec.config as { children?: { block_id?: unknown }[] } | null)?.children;
      if (Array.isArray(children)) {
        for (const child of children) {
          if (child && typeof child.block_id === "string") claimed.add(child.block_id);
        }
      }
    } else if (spec.type === "notebook") {
      const sections = (spec.config as { sections?: { kind?: unknown; canvas_block_id?: unknown }[] } | null)?.sections;
      if (Array.isArray(sections)) {
        for (const section of sections) {
          if (section?.kind === "ink" && typeof section.canvas_block_id === "string") claimed.add(section.canvas_block_id);
        }
      }
    }
  }
  return claimed;
}

/** How long a `show_block` highlight stays on. */
export const HIGHLIGHT_MS = 2400;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function CompositePanel(props: PanelProps) {
  const layout = panelLayoutOf(props.agent);
  const blockIds = useRef<Set<string>>(new Set());
  blockIds.current = new Set(layout.blocks.map((spec) => spec.id));
  // V6-10: blocks a `layout` claims render only inside it (`LayoutBlock`), not
  // in this flat flow too. `blockIds` above stays the full set — `show_block`
  // may still target a claimed child (it works when the layout is `columns`,
  // or the child's `tabs` pane is already active; see the file docblock).
  const claimed = useMemo(() => claimedChildIds(layout.blocks), [layout.blocks]);
  const topLevelBlocks = useMemo(() => layout.blocks.filter((spec) => !claimed.has(spec.id)), [layout.blocks, claimed]);

  const [highlight, setHighlight] = useState<string | null>(null);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const highlightTimer = useRef<number | null>(null);

  const reveal = useCallback((blockId: string, focus: boolean) => {
    const frame = document.getElementById(blockDomId(blockId));
    if (!frame) return false;
    frame.scrollIntoView?.({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "nearest" });
    const field = focus ? frame.querySelector<HTMLElement>("input, select, textarea, button") : null;
    (field ?? frame).focus({ preventScroll: true });
    setHighlight(blockId);
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
    return true;
  }, []);

  useEffect(
    () =>
      subscribeCompositeRequests((event: CompositeRequestEvent) => {
        if (event.kind === "navigate") {
          setPendingUrl(event.url);
          return true;
        }
        // `snapshot` (V6-14) is answered by the claimed `canvas` block itself
        // (`blocks/canvas.tsx`'s own `subscribeCompositeRequests` listener), not here.
        if (event.kind !== "reveal") return false;
        if (!blockIds.current.has(event.blockId)) return false;
        // A `form` request can arrive before the patch that renders the form;
        // wait a frame so the fields exist when we focus.
        window.requestAnimationFrame(() => reveal(event.blockId, event.focus));
        return true;
      }),
    [reveal],
  );

  useEffect(
    () => () => {
      if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    },
    [],
  );

  const wide = layout.layout === "wide";
  // V5-15/V5-17: the persistent "you're talking to an AI assistant" banner —
  // panel-level (`./banner.tsx`) so it stays put while the blocks scroll.
  const banner = bannerTextOf(layout.blocks, props.state.blocks);

  return (
    <BlockHighlightProvider value={highlight}>
      <div
        data-testid="composite-panel"
        data-layout={layout.layout}
        className={cn("flex h-full flex-col overflow-y-auto", wide && "xl:grid xl:auto-rows-min xl:grid-cols-2 xl:content-start")}
      >
        {banner ? (
          <div className={cn(wide && "xl:col-span-2")}>
            <ConsentBanner text={banner} />
          </div>
        ) : null}
        {layout.blocks.length === 0 ? (
          <div className="px-4 py-4">
            <PanelEmpty>This panel has no blocks yet.</PanelEmpty>
          </div>
        ) : (
          topLevelBlocks.map((spec) => (
            <div
              key={spec.id}
              className={cn(
                "border-border min-w-0 border-t first:border-t-0",
                wide && WIDE_SPAN.has(spec.type) && "xl:col-span-2",
              )}
            >
              <Block spec={spec} highlighted={highlight === spec.id} {...props} />
            </div>
          ))
        )}

        <Dialog open={pendingUrl !== null} onOpenChange={(open) => !open && setPendingUrl(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Open this link?</DialogTitle>
              <DialogDescription>
                {props.agent.name} wants to open a page in a new tab.
              </DialogDescription>
            </DialogHeader>
            <p className="bg-muted text-muted-foreground rounded-md px-3 py-2 font-mono text-[0.8125rem] break-all">
              {pendingUrl}
            </p>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setPendingUrl(null)}>
                Stay here
              </Button>
              <Button
                type="button"
                onClick={() => {
                  if (pendingUrl) window.open(pendingUrl, "_blank", "noopener,noreferrer");
                  setPendingUrl(null);
                }}
              >
                Open link
                <Icon as={ExternalLinkIcon} size="sm" />
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </BlockHighlightProvider>
  );
}

export const COMPOSITE_PANEL: PanelDefinition = {
  id: COMPOSITE_PANEL_ID,
  title: "Session",
  Component: CompositePanel,
  layout: "side",
  layoutFor: (agent) => panelLayoutOf(agent).layout,
  blocksAware: true,
  handleRequest: handleCompositeRequest,
};
