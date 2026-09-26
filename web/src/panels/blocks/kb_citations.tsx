"use client";

/**
 * `kb_citations` block — the knowledge-base passages behind the agent's last
 * answer. The worker replaces `items` after every `search_knowledge` hit
 * (`set /blocks/<id>/items`), so this always shows the latest answer's
 * sources, best match first as the worker orders them.
 *
 * Tapping a citation sends `block_action {name: "open_citation", data:
 * {chunk_id}}` (CONTRACTS-V2 §4.4, R-V5-5): the worker opens the cited page
 * in a `document` block when the session holds the source as an asset, or
 * answers `{opened: false, reason, citation?}` — handled here by showing the
 * passage (filename, page, heading path, text) in a dialog, since there is
 * nowhere else on the panel to preview it (V5-12).
 */
import * as React from "react";
import { useState } from "react";

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { KbCitation, KbCitationsBlockState } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

function scoreLabel(score: number): string {
  if (!Number.isFinite(score)) return "";
  return score <= 1 ? `${Math.round(score * 100)}% match` : `score ${score.toFixed(2)}`;
}

/** "page 3 · Deductibles › Wind and hail" — whichever locators the citation carries. */
function locatorLine(item: Pick<KbCitation, "page" | "heading_path">): string | null {
  const parts: string[] = [];
  if (typeof item.page === "number") parts.push(`page ${item.page}`);
  if (Array.isArray(item.heading_path) && item.heading_path.length > 0) parts.push(item.heading_path.join(" › "));
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The worker's `open_citation` result shape (`ui/blocks.py::open_citation`). */
interface OpenCitationResult {
  opened?: string | false;
  reason?: "unknown_citation" | "no_document_block" | "no_source" | string;
  citation?: Partial<KbCitation>;
}

function CitationRow({
  item,
  index,
  opening,
  onOpen,
}: {
  item: KbCitation;
  index: number;
  opening: boolean;
  onOpen: () => void;
}) {
  const long = item.text.length > 220;
  const locator = locatorLine(item);
  return (
    <li data-slot="block-citation" className="flex gap-2.5">
      <span
        aria-hidden="true"
        className="bg-muted text-muted-foreground mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-xs text-xs font-medium tabular-nums"
      >
        {index + 1}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
          <button
            type="button"
            onClick={onOpen}
            disabled={opening}
            title="Open this source"
            className="focus-visible:ring-ring min-w-0 truncate rounded-xs text-left underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:outline-none disabled:opacity-60"
          >
            {item.filename}
          </button>
          <span className="text-muted-foreground text-xs font-normal tabular-nums">{scoreLabel(item.score)}</span>
        </p>
        {locator && <p className="text-muted-foreground mt-0.5 text-[0.6875rem]">{locator}</p>}
        {long ? (
          <details className="group mt-0.5">
            <summary className="text-muted-foreground focus-visible:ring-ring cursor-pointer list-none rounded-xs text-[0.8125rem] leading-snug focus-visible:ring-2 focus-visible:outline-none">
              <span className="group-open:hidden">{item.text.slice(0, 220).trimEnd()}… <span className="text-foreground underline underline-offset-2">More</span></span>
              <span className="text-foreground hidden underline underline-offset-2 group-open:inline">Less</span>
            </summary>
            <p className="text-muted-foreground mt-1 text-[0.8125rem] leading-snug whitespace-pre-wrap">{item.text}</p>
          </details>
        ) : (
          <p className="text-muted-foreground mt-0.5 text-[0.8125rem] leading-snug whitespace-pre-wrap">{item.text}</p>
        )}
      </div>
    </li>
  );
}

export function KbCitationsBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<KbCitationsBlockState>) {
  const items = Array.isArray(data.items) ? data.items : [];
  const [opening, setOpening] = useState<string | null>(null);
  const [preview, setPreview] = useState<Partial<KbCitation> | null>(null);

  async function handleOpen(item: KbCitation) {
    setOpening(item.chunk_id);
    try {
      const result = (await panel.perform({
        action: "block_action",
        payload: { block_id: spec.id, name: "open_citation", data: { chunk_id: item.chunk_id } },
      })) as OpenCitationResult | undefined;
      // `opened: "document"` means a `document` block already shows the page;
      // nothing further to do here. `citation` is only absent for
      // `unknown_citation` (stale tap on a citation the block no longer has).
      if (result?.opened === false && result.citation) {
        setPreview(result.citation);
      }
    } catch {
      // Couldn't reach the agent — nothing to preview either.
    } finally {
      setOpening(null);
    }
  }

  return (
    <BlockFrame spec={spec} title={title} count={items.length} highlighted={highlighted}>
      {items.length === 0 ? (
        <PanelEmpty>Sources appear here when the agent answers from the knowledge base.</PanelEmpty>
      ) : (
        <ol data-slot="block-citations" className="space-y-3">
          {items.map((item, index) => (
            <CitationRow
              key={item.chunk_id}
              item={item}
              index={index}
              opening={opening === item.chunk_id}
              onOpen={() => void handleOpen(item)}
            />
          ))}
        </ol>
      )}
      <Dialog open={preview !== null} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>{preview?.filename ?? "Source passage"}</DialogTitle>
            <DialogDescription>{locatorLine(preview ?? {}) ?? "A passage from the knowledge base."}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{preview?.text}</p>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </BlockFrame>
  );
}
