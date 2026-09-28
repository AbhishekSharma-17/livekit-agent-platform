"use client";

/**
 * `markdown` block — longer text the agent shows (`show_text`, CONTRACTS-V2
 * §4.4, V5-08 → V5-12): a strict Markdown subset, never raw HTML, links only
 * when `config.allow_links`, images only from the session's own assets.
 *
 * Renders through the shared `SafeMarkdown` helper (`@/lib/safe-markdown`,
 * V6-10 ask #333; S6-8, ask #81) rather than its own `<Streamdown>` setup —
 * this file used to carry its own copy of the strip-HTML remark plugin and
 * the link/image `components`, exactly the second source of truth the
 * security review flagged (the transcript's bare `<Streamdown>` was the
 * other). `SafeMarkdown` is `https:`-only for links (stricter than this
 * block's own former `http:`/`mailto:` allowance — no agent- or
 * caller-authored panel text plausibly needs an unencrypted or mail link
 * enough to keep the wider allowance) and, as before, only ever renders an
 * image whose `src` is an asset id already resolved in `panel.assets`.
 *
 * Loaded lazily by `<Block>` (session bundle budget: `streamdown` — which
 * `SafeMarkdown` itself pulls in — stays out of the main chunk for a session
 * whose panel has no `markdown` block).
 */
import * as React from "react";

import type { MarkdownBlockState } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { SafeMarkdown } from "@/lib/safe-markdown";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function MarkdownBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<MarkdownBlockState>) {
  const markdown = typeof data.markdown === "string" ? data.markdown : "";
  const allowLinks = (spec.config as { allow_links?: unknown } | null)?.allow_links === true;

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {markdown.trim() === "" ? (
        <PanelEmpty>The agent hasn&rsquo;t written anything here yet.</PanelEmpty>
      ) : (
        <div data-slot="block-markdown" className="flex flex-col gap-1.5">
          {typeof data.title === "string" && data.title && <h4 className="text-sm font-medium">{data.title}</h4>}
          <SafeMarkdown
            text={markdown}
            assets={panel.assets}
            allowLinks={allowLinks}
            className="max-w-none text-sm leading-relaxed [&_blockquote]:text-muted-foreground [&_blockquote]:italic [&_code]:bg-muted [&_code]:rounded-xs [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.8125rem] [&_h1]:mb-2 [&_h1]:mt-0 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1.5 [&_h2]:mt-3 [&_h2]:text-[0.9375rem] [&_h2]:font-semibold [&_li]:my-0.5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5"
          />
          {typeof data.updated_at === "number" && (
            <span className="text-muted-foreground text-[0.6875rem]">Updated {formatTime(data.updated_at)}</span>
          )}
        </div>
      )}
    </BlockFrame>
  );
}

export default MarkdownBlock;
