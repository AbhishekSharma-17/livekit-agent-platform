"use client";

/**
 * `markdown` block — longer text the agent shows (`show_text`, CONTRACTS-V2
 * §4.4, V5-08 → V5-12): a strict Markdown subset, never raw HTML, links only
 * when `config.allow_links`, images only from the session's own assets.
 *
 * Uses `streamdown` — already a dependency (the insurance notebook's
 * adjuster packet, `document.tsx`'s fetched-Markdown fallback) — with one
 * addition to its remark pipeline: `stripHtmlNodesPlugin` below removes every
 * `html`-typed mdast node (embedded `<tag>` text, block or inline) before it
 * ever reaches hast, so it renders as nothing rather than as literal text or
 * interpreted markup — "no raw HTML" holds for real Markdown syntax alone. A
 * fenced code block's contents are a `code` node, never `html`, so an example
 * `<div>` inside a code fence still shows verbatim, as code.
 *
 * `streamdown` bundles `rehype-harden` for link/image origin checks, but
 * (a) its default config there is "allow every link and image"
 * (`allowedLinkPrefixes: ["*"]`), and (b) it also actively *blocks* an
 * `<img>` whose `src` doesn't parse as a URL against that allow-list — which
 * an asset id like `frame-stove` never does, replacing it with its own
 * "Image blocked" placeholder before this file's `components.img` ever runs.
 * So `harden` (and `raw`, now redundant — see `stripHtmlNodesPlugin` above)
 * are left out of `rehypePlugins`; only `sanitize` stays, a defensive
 * backstop. This file does its own link/image vetting through `components`:
 * an `<img>` only ever resolves an asset id already in `panel.assets` (never
 * an arbitrary URL), and an `<a>` only renders — `target="_blank"
 * rel="noopener noreferrer"`, `http:`/`https:`/`mailto:` only — when
 * `allow_links` is set; otherwise its text shows, never a clickable control.
 *
 * Loaded lazily by `<Block>` (session bundle budget: `streamdown` stays out
 * of the main chunk for a session whose panel has no `markdown` block).
 */
import * as React from "react";
import { useMemo } from "react";
import { defaultRehypePlugins, defaultRemarkPlugins, Streamdown, type Components, type StreamdownProps } from "streamdown";

import type { MarkdownBlockState } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

interface MdastParent {
  children?: unknown[];
}

function isParent(node: unknown): node is MdastParent {
  return typeof node === "object" && node !== null && Array.isArray((node as MdastParent).children);
}

/** Drop every `html` mdast node (see the file docblock) — depth-first, in place. */
function stripHtmlNodes(node: unknown): void {
  if (!isParent(node)) return;
  node.children = node.children!.filter((child) => (child as { type?: string })?.type !== "html");
  for (const child of node.children) stripHtmlNodes(child);
}

function stripHtmlNodesPlugin() {
  return stripHtmlNodes;
}

const REMARK_PLUGINS: NonNullable<StreamdownProps["remarkPlugins"]> = [
  ...Object.values(defaultRemarkPlugins),
  stripHtmlNodesPlugin,
];

/** Only `sanitize` — no `raw` (nothing left for it, see above) and no `harden` (see above). */
const REHYPE_PLUGINS: NonNullable<StreamdownProps["rehypePlugins"]> = [defaultRehypePlugins.sanitize];

const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function safeHref(href: unknown): string | null {
  if (typeof href !== "string" || href === "") return null;
  try {
    return SAFE_LINK_PROTOCOLS.has(new URL(href).protocol) ? href : null;
  } catch {
    return null;
  }
}

type LinkProps = React.ComponentProps<"a"> & { node?: unknown };
type ImgProps = React.ComponentProps<"img"> & { node?: unknown };

/** `allow_links`: a safe, new-tab `<a>`, or its text unwrapped when the href fails the check. */
function AllowedLink({ href, children }: LinkProps) {
  const safe = safeHref(href);
  if (!safe) return <>{children}</>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
      {children}
    </a>
  );
}

/** `!allow_links` (the default): link text, never a clickable control. */
function LinkText({ children }: LinkProps) {
  return <>{children}</>;
}

/** Images only from the session's own assets — `src` names an asset id, resolved through `panel.assets`. */
function assetImage(assets: Map<string, string>) {
  return function AssetImage({ src, alt }: ImgProps) {
    const url = typeof src === "string" ? assets.get(src) : undefined;
    if (!url) return null;
    // eslint-disable-next-line @next/next/no-img-element -- a resolved session-asset blob URL
    return <img src={url} alt={alt ?? ""} className="max-w-full rounded-md" />;
  };
}

const IDENTITY_URL_TRANSFORM = (url: string): string => url;

export function MarkdownBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<MarkdownBlockState>) {
  const markdown = typeof data.markdown === "string" ? data.markdown : "";
  const allowLinks = (spec.config as { allow_links?: unknown } | null)?.allow_links === true;
  const components: Components = useMemo(
    () => ({ img: assetImage(panel.assets), a: allowLinks ? AllowedLink : LinkText }),
    [panel.assets, allowLinks],
  );

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {markdown.trim() === "" ? (
        <PanelEmpty>The agent hasn&rsquo;t written anything here yet.</PanelEmpty>
      ) : (
        <div data-slot="block-markdown" className="flex flex-col gap-1.5">
          {typeof data.title === "string" && data.title && <h4 className="text-sm font-medium">{data.title}</h4>}
          <div className="max-w-none text-sm leading-relaxed [&_blockquote]:text-muted-foreground [&_blockquote]:italic [&_code]:bg-muted [&_code]:rounded-xs [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.8125rem] [&_h1]:mb-2 [&_h1]:mt-0 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1.5 [&_h2]:mt-3 [&_h2]:text-[0.9375rem] [&_h2]:font-semibold [&_li]:my-0.5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5">
            <Streamdown
              remarkPlugins={REMARK_PLUGINS}
              rehypePlugins={REHYPE_PLUGINS}
              components={components}
              urlTransform={IDENTITY_URL_TRANSFORM}
            >
              {markdown}
            </Streamdown>
          </div>
          {typeof data.updated_at === "number" && (
            <span className="text-muted-foreground text-[0.6875rem]">Updated {formatTime(data.updated_at)}</span>
          )}
        </div>
      )}
    </BlockFrame>
  );
}

export default MarkdownBlock;
