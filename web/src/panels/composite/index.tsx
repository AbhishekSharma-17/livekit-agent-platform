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
 */
import * as React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
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
import { cn } from "@/lib/utils";
import { Block, blockDomId } from "@/panels/blocks";
import { PanelEmpty } from "@/panels/generic/blocks";
import type { PanelDefinition, PanelProps } from "@/panels/registry";

import type { BlockType } from "./layout";
import { COMPOSITE_PANEL_ID, panelLayoutOf } from "./layout";
import { handleCompositeRequest, subscribeCompositeRequests, type CompositeRequestEvent } from "./requests";

/** Blocks that take the full width of a `wide` layout. */
const WIDE_SPAN: ReadonlySet<BlockType> = new Set<BlockType>(["status", "document", "table", "transcript", "video", "form"]);

/** How long a `show_block` highlight stays on. */
export const HIGHLIGHT_MS = 2400;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function CompositePanel(props: PanelProps) {
  const layout = panelLayoutOf(props.agent);
  const blockIds = useRef<Set<string>>(new Set());
  blockIds.current = new Set(layout.blocks.map((spec) => spec.id));

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

  return (
    <div
      data-testid="composite-panel"
      data-layout={layout.layout}
      className={cn("flex h-full flex-col overflow-y-auto", wide && "xl:grid xl:auto-rows-min xl:grid-cols-2 xl:content-start")}
    >
      {layout.blocks.length === 0 ? (
        <div className="px-4 py-4">
          <PanelEmpty>This panel has no blocks yet.</PanelEmpty>
        </div>
      ) : (
        layout.blocks.map((spec) => (
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
