"use client";

/**
 * `<SafeMarkdown>` — the one strict Markdown renderer every panel surface
 * routes through (V6-10, ask #333: `https` links only, platform-hosted
 * images only): `document.tsx`'s fetched-Markdown fallback and the `notebook`
 * block's `text` sections (the insurance pack's packet dialog was removed in V6-22).
 *
 * A trimmed, stricter port of `panels/blocks/markdown.tsx`'s own Streamdown
 * setup (kept there unchanged — not this package's file; see docs/v6/_asks.md):
 *
 * - `stripHtmlNodesPlugin` drops every `html`-typed mdast node before it ever
 *   reaches hast, so raw `<tag>` text renders as nothing, not literal text or
 *   interpreted markup. A fenced code block's contents stay a `code` node, so
 *   an example `<div>` inside a code fence still shows verbatim.
 * - Only `rehype-sanitize` runs (no `raw`, nothing left for it to do; no
 *   `harden` — its own origin allow-list defaults to "allow everything" and
 *   would block a legitimate asset image outright). Link and image safety are
 *   this file's own `components`.
 * - An `<a>` renders only when `allowLinks` is set *and* its `href` is
 *   `https:` (never `http:`, `javascript:`, `data:` or `mailto:` — stricter
 *   than `markdown.tsx`, which also allows `http:`/`mailto:`); otherwise (or
 *   when links are off) its text shows, never a clickable control.
 * - An `<img>` renders only when its `src` names an asset id already resolved
 *   in the `assets` map (a session asset the browser already has bytes for);
 *   any other `src` — a third-party URL included — renders nothing. Pass no
 *   `assets` map at all (the default) to block every image, for a caller not
 *   in this session (`packet-dialog.tsx`, the adjuster packet).
 */
import * as React from "react";
import { useMemo } from "react";
import {
  defaultRehypePlugins,
  defaultRemarkPlugins,
  Streamdown,
  type Components,
  type StreamdownProps,
} from "streamdown";

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

/** Only `sanitize` — no `raw`, no `harden` (see the file docblock). */
const REHYPE_PLUGINS: NonNullable<StreamdownProps["rehypePlugins"]> = [defaultRehypePlugins.sanitize];

type LinkProps = React.ComponentProps<"a"> & { node?: unknown };
type ImgProps = React.ComponentProps<"img"> & { node?: unknown };

/** `https:` only — stricter than `markdown.tsx`'s `safeHref` (no `http:`/`mailto:`). */
function safeHttpsHref(href: unknown): string | null {
  if (typeof href !== "string" || href === "") return null;
  try {
    return new URL(href).protocol === "https:" ? href : null;
  } catch {
    return null;
  }
}

/** `allowLinks`: a safe, new-tab `<a>`, or its text unwrapped when the href fails the check. */
function AllowedLink({ href, children }: LinkProps) {
  const safe = safeHttpsHref(href);
  if (!safe) return <>{children}</>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
      {children}
    </a>
  );
}

/** `!allowLinks` (the default): link text, never a clickable control. */
function LinkText({ children }: LinkProps) {
  return <>{children}</>;
}

/** Images only from the given asset map — `src` names an asset id, resolved through it. */
function assetImage(assets: Map<string, string>) {
  return function AssetImage({ src, alt }: ImgProps) {
    const url = typeof src === "string" ? assets.get(src) : undefined;
    if (!url) return null;
    // eslint-disable-next-line @next/next/no-img-element -- a resolved session-asset blob URL
    return <img src={url} alt={alt ?? ""} className="max-w-full rounded-md" />;
  };
}

const IDENTITY_URL_TRANSFORM = (url: string): string => url;

const EMPTY_ASSETS: Map<string, string> = new Map();

export interface SafeMarkdownProps {
  /** The Markdown text (never raw HTML; see the file docblock). */
  text: string;
  /**
   * `asset_id` → object/signed URL for the images this text may reference.
   * Omit to block every image (a caller with no session assets, e.g. the
   * adjuster packet dialog).
   */
  assets?: Map<string, string>;
  /** Show `https://` links as clickable; default `false` (link text only). */
  allowLinks?: boolean;
  className?: string;
}

/** The strict Markdown renderer every panel surface routes through (see the file docblock). */
export function SafeMarkdown({ text, assets, allowLinks = false, className }: SafeMarkdownProps) {
  const resolvedAssets = assets ?? EMPTY_ASSETS;
  const components: Components = useMemo(
    () => ({ img: assetImage(resolvedAssets), a: allowLinks ? AllowedLink : LinkText }),
    [resolvedAssets, allowLinks],
  );
  return (
    <div className={className}>
      <Streamdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
        urlTransform={IDENTITY_URL_TRANSFORM}
      >
        {text}
      </Streamdown>
    </div>
  );
}

export default SafeMarkdown;
