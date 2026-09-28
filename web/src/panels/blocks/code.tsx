"use client";

/**
 * `code` block — read-only code or text in a fixed-width font (`show_code`,
 * V6-23, D-V6-20, ask #215). Nothing shown here is ever run.
 *
 * **Never Markdown.** `data.code` is rendered as the text of a `<pre><code>`
 * — the code is a plain text child node, never assembled into a Markdown
 * string wrapped in a fixed ```` ``` ```` fence and handed to a Markdown
 * renderer: a payload that itself contains ```` ``` ```` would break out of
 * a hand-built fence, and even a well-escaped fence buys nothing here since
 * a `<pre><code>` text child is already inert (React escapes it like any
 * other text; no HTML or Markdown syntax inside `data.code` is ever
 * interpreted).
 */
import * as React from "react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { CodeBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

function CopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard access (an insecure context, a denied permission) — nothing more to do.
    }
  }
  return (
    <Button type="button" size="sm" variant="ghost" onClick={() => void copy()}>
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

export function CodeBlock({ spec, data, title, highlighted }: BlockRenderProps<CodeBlockState>) {
  const code = typeof data.code === "string" ? data.code : "";
  const wrap = (spec.config as { wrap?: unknown } | null)?.wrap === true;

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {code === "" ? (
        <PanelEmpty>Nothing to show yet.</PanelEmpty>
      ) : (
        <div data-slot="block-code" className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {typeof data.title === "string" && data.title && <h4 className="text-sm font-medium">{data.title}</h4>}
              {typeof data.language === "string" && data.language && (
                <span className="bg-muted text-muted-foreground rounded-xs px-1.5 py-0.5 text-[0.6875rem] font-medium uppercase tracking-wide">
                  {data.language}
                </span>
              )}
            </div>
            <CopyButton code={code} />
          </div>
          <pre
            data-slot="block-code-pre"
            className={cn(
              "bg-muted/50 border-border overflow-x-auto rounded-md border p-3 font-mono text-[0.8125rem] leading-relaxed",
              wrap ? "break-words whitespace-pre-wrap" : "whitespace-pre",
            )}
          >
            <code>{code}</code>
          </pre>
        </div>
      )}
    </BlockFrame>
  );
}

export default CodeBlock;
