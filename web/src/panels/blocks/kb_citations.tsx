"use client";

/**
 * `kb_citations` block — the knowledge-base passages behind the agent's last
 * answer. The worker replaces `items` after every `search_knowledge` hit
 * (`set /blocks/<id>/items`), so this always shows the latest answer's
 * sources, best match first as the worker orders them.
 */
import * as React from "react";

import type { KbCitation, KbCitationsBlockState } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

function scoreLabel(score: number): string {
  if (!Number.isFinite(score)) return "";
  return score <= 1 ? `${Math.round(score * 100)}% match` : `score ${score.toFixed(2)}`;
}

function CitationRow({ item, index }: { item: KbCitation; index: number }) {
  const long = item.text.length > 220;
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
          <span className="min-w-0 truncate">{item.filename}</span>
          <span className="text-muted-foreground text-xs font-normal tabular-nums">{scoreLabel(item.score)}</span>
        </p>
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

export function KbCitationsBlock({ spec, data, title, highlighted }: BlockRenderProps<KbCitationsBlockState>) {
  const items = Array.isArray(data.items) ? data.items : [];
  return (
    <BlockFrame spec={spec} title={title} count={items.length} highlighted={highlighted}>
      {items.length === 0 ? (
        <PanelEmpty>Sources appear here when the agent answers from the knowledge base.</PanelEmpty>
      ) : (
        <ol data-slot="block-citations" className="space-y-3">
          {items.map((item, index) => (
            <CitationRow key={item.chunk_id} item={item} index={index} />
          ))}
        </ol>
      )}
    </BlockFrame>
  );
}
