"use client";

/**
 * `document` block — a PDF (pdf.js), an image or Markdown, one page at a
 * time, with the agent's highlights (CONTRACTS-V2 §4.4, `show_document`).
 *
 * State: `{asset_id | url, page (1-based), highlights: [{page, bbox, note}]}`.
 * `bbox` is `[x0, y0, x1, y1]` in page-relative 0..1 coordinates, origin top
 * left; `show_document` writes a note as the whole page (`[0,0,1,1]`), which
 * is listed under the page rather than drawn as a box.
 *
 * Sources: an `asset_id` resolves through the envelope's `assets` (mime) and
 * the asset object URL; a `url` must be `https://` and its kind comes from the
 * path's extension. Nothing is ever put in an iframe.
 *
 * Loaded lazily by `<Block>`; pdf.js is a second, PDF-only split.
 */
import * as React from "react";
import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { ChevronLeftIcon, ChevronRightIcon, ExternalLinkIcon } from "lucide-react";
import { Streamdown } from "streamdown";

import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import type { DocumentBlockState, DocumentHighlight } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const PdfPage = lazy(() => import("./pdf-page"));

export type DocumentKind = "pdf" | "image" | "markdown" | "other";

export interface ResolvedDocument {
  kind: DocumentKind;
  /** Object URL (asset) or `https://` URL; `null` while an asset's bytes are in flight. */
  src: string | null;
  name: string;
  /** Where "Open" points: only ever an `https://` URL. */
  external: string | null;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|svg)$/i;
const MARKDOWN_EXT = /\.(md|markdown|mdown|txt)$/i;

function kindFromMime(mime: string): DocumentKind {
  if (mime === "application/pdf") return "pdf";
  if (mime.startsWith("image/")) return "image";
  if (mime === "text/markdown" || mime === "text/x-markdown" || mime === "text/plain") return "markdown";
  return "other";
}

function kindFromUrl(url: URL): DocumentKind {
  const path = url.pathname;
  if (/\.pdf$/i.test(path)) return "pdf";
  if (IMAGE_EXT.test(path)) return "image";
  if (MARKDOWN_EXT.test(path)) return "markdown";
  return "other";
}

/** Work out what to render for the block's state. `null` = nothing to show yet. */
export function resolveDocument(
  data: DocumentBlockState,
  assets: { asset_id: string; mime: string; caption?: string | null }[],
  urls: Map<string, string>,
): ResolvedDocument | null {
  if (data.asset_id) {
    const ref = assets.find((asset) => asset.asset_id === data.asset_id);
    return {
      kind: ref ? kindFromMime(ref.mime) : "other",
      src: urls.get(data.asset_id) ?? null,
      name: ref?.caption || data.asset_id,
      external: null,
    };
  }
  if (typeof data.url === "string" && data.url) {
    let url: URL;
    try {
      url = new URL(data.url);
    } catch {
      return { kind: "other", src: null, name: data.url, external: null };
    }
    if (url.protocol !== "https:") return { kind: "other", src: null, name: data.url, external: null };
    const name = decodeURIComponent(url.pathname.split("/").filter(Boolean).pop() ?? url.hostname);
    return { kind: kindFromUrl(url), src: url.toString(), name, external: url.toString() };
  }
  return null;
}

/** `bbox` arrives as `unknown[4]` in the generated types: coerce to numbers. */
function boxOf(highlight: DocumentHighlight): [number, number, number, number] | null {
  const raw = Array.isArray(highlight.bbox) ? highlight.bbox : [];
  const box = raw.map((v) => (typeof v === "number" ? v : Number(v)));
  if (box.length !== 4 || box.some((v) => !Number.isFinite(v))) return null;
  const [x0, y0, x1, y1] = box.map((v) => Math.min(1, Math.max(0, v)));
  if (x1 <= x0 || y1 <= y0) return null;
  return [x0, y0, x1, y1];
}

function isWholePage(box: [number, number, number, number]): boolean {
  return box[0] <= 0 && box[1] <= 0 && box[2] >= 1 && box[3] >= 1;
}

function HighlightBoxes({ highlights }: { highlights: DocumentHighlight[] }) {
  return (
    <>
      {highlights.map((highlight, index) => {
        const box = boxOf(highlight);
        if (!box || isWholePage(box)) return null;
        const [x0, y0, x1, y1] = box;
        return (
          <span
            key={index}
            data-slot="block-document-highlight"
            aria-hidden="true"
            title={highlight.note ?? undefined}
            className="border-warning bg-warning/20 pointer-events-none absolute rounded-xs border-2"
            style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
          />
        );
      })}
    </>
  );
}

function MarkdownDocument({ src }: { src: string }) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setText(null);
    setFailed(false);
    fetch(src)
      .then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [src]);
  if (failed) return <PanelEmpty>Couldn&rsquo;t load this document.</PanelEmpty>;
  if (text === null) return <PanelEmpty>Loading…</PanelEmpty>;
  return (
    <div data-slot="block-document-markdown" className="max-h-[28rem] overflow-y-auto text-sm leading-relaxed">
      <Streamdown>{text}</Streamdown>
    </div>
  );
}

export function DocumentBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<DocumentBlockState>) {
  const doc = useMemo(
    () => resolveDocument(data, panel.state.assets ?? [], panel.assets),
    [data, panel.state.assets, panel.assets],
  );
  const agentPage = typeof data.page === "number" && data.page >= 1 ? Math.floor(data.page) : 1;
  // The viewer can page locally; a new page (or document) from the agent wins.
  const [page, setPage] = useState(agentPage);
  const [pageCount, setPageCount] = useState<number | null>(null);
  const docKey = `${doc?.src ?? ""}|${agentPage}`;
  const [seenKey, setSeenKey] = useState(docKey);
  if (seenKey !== docKey) {
    setSeenKey(docKey);
    setPage(agentPage);
  }

  const highlights = Array.isArray(data.highlights) ? data.highlights : [];
  const pageHighlights = doc?.kind === "pdf" ? highlights.filter((h) => h.page === page) : highlights;
  const notes = pageHighlights.filter((h) => h.note);

  const nav =
    doc?.kind === "pdf" ? (
      <>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          <Icon as={ChevronLeftIcon} size="sm" />
        </Button>
        <span className="text-muted-foreground text-xs tabular-nums" aria-live="polite">
          {pageCount ? `${page} / ${pageCount}` : `Page ${page}`}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label="Next page"
          disabled={pageCount !== null && page >= pageCount}
          onClick={() => setPage((p) => (pageCount ? Math.min(pageCount, p + 1) : p + 1))}
        >
          <Icon as={ChevronRightIcon} size="sm" />
        </Button>
      </>
    ) : doc?.external ? (
      <a
        href={doc.external}
        target="_blank"
        rel="noopener noreferrer"
        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded-xs text-xs focus-visible:ring-2 focus-visible:outline-none"
      >
        Open
        <Icon as={ExternalLinkIcon} size="sm" />
        <span className="sr-only">{doc.name} in a new tab</span>
      </a>
    ) : null;

  let body: React.ReactNode;
  if (!doc) {
    body = <PanelEmpty>The agent hasn&rsquo;t opened a document yet.</PanelEmpty>;
  } else if (!doc.src) {
    body = (
      <PanelEmpty>{doc.external === null && !data.asset_id ? "This document link can’t be opened." : "Receiving the document…"}</PanelEmpty>
    );
  } else if (doc.kind === "pdf") {
    body = (
      <Suspense fallback={<PanelEmpty>Loading…</PanelEmpty>}>
        <PdfPage url={doc.src} page={page} onPageCount={setPageCount}>
          <HighlightBoxes highlights={pageHighlights} />
        </PdfPage>
      </Suspense>
    );
  } else if (doc.kind === "image") {
    body = (
      <div data-slot="block-document-image" className="bg-muted/40 relative overflow-hidden rounded-md">
        {/* eslint-disable-next-line @next/next/no-img-element -- blob: object URL or an agent-chosen https URL */}
        <img src={doc.src} alt={doc.name} className="block h-auto w-full" />
        <HighlightBoxes highlights={pageHighlights} />
      </div>
    );
  } else if (doc.kind === "markdown") {
    body = <MarkdownDocument src={doc.src} />;
  } else {
    body = (
      <PanelEmpty>
        {doc.external ? (
          <a href={doc.external} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
            Open {doc.name}
          </a>
        ) : (
          "This file type can’t be shown here."
        )}
      </PanelEmpty>
    );
  }

  return (
    <BlockFrame spec={spec} title={title} action={nav} highlighted={highlighted}>
      {doc && <p className="text-muted-foreground mb-2 truncate text-xs">{doc.name}</p>}
      {body}
      {notes.length > 0 && (
        <ul data-slot="block-document-notes" className="mt-2.5 space-y-1.5">
          {notes.map((highlight, index) => (
            <li key={index} className="flex gap-2 text-sm leading-snug">
              <span aria-hidden="true" className="bg-warning mt-1.5 size-1.5 shrink-0 rounded-full" />
              <span className="min-w-0 break-words">{highlight.note}</span>
            </li>
          ))}
        </ul>
      )}
    </BlockFrame>
  );
}

export default DocumentBlock;
