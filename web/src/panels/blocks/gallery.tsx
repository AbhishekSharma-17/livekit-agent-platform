"use client";

/**
 * `gallery` block — images delivered on `lkap.ui.asset` (`asset_ids`), e.g.
 * every `pin_frame` photo and every uploaded `image/*` file (the worker
 * appends each one to every gallery block in the same patch as its `/assets`
 * append). `selected` is agent-owned (drawn with the brand ring); tapping a
 * tile only enlarges it for the viewer.
 *
 * A `stored` asset (V5-19 `AssetRef.stored`) also has a signed download URL
 * once the session has ended: `session-panel-tab.tsx`'s read-only snapshot
 * has no room (no `lkap.ui.asset` bytes ever arrive there), so it passes a
 * `panel.assets` map built from `GET /v1/sessions/{id}/assets`'s signed
 * `url`s instead of blob URLs — this block renders either the same way, via
 * `panel.assets.get(asset_id)` (docs/v5/_asks.md #131: "renders stored
 * assets through the signed URL when stored").
 *
 * HEIC/HEIF (ask #134): browsers cannot decode either in an `<img>`, so a
 * tile of that type never attempts one — a plain "Preview not available"
 * line instead of a broken image, even once a URL exists.
 */
import * as React from "react";
import { useMemo, useState } from "react";

import type { AssetRef, GalleryBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { AssetTile, PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

/** Image types no browser renders in `<img>` (ask #134); everything else `AssetTile` handles itself. */
const UNPREVIEWABLE_IMAGE_TYPES: ReadonlySet<string> = new Set(["image/heic", "image/heif"]);

/** Whether `AssetTile` can actually draw `mime` as a picture. */
export function isPreviewableImage(mime: string): boolean {
  return mime.startsWith("image/") && !UNPREVIEWABLE_IMAGE_TYPES.has(mime);
}

/**
 * One file tile: a real preview through `AssetTile` when the browser can
 * render the type, else a plain "Preview not available" line — never a
 * broken `<img>`. Shared with `upload.tsx`'s file list.
 */
export function FileTile({ asset, url }: { asset: AssetRef; url?: string }) {
  if (isPreviewableImage(asset.mime)) return <AssetTile asset={asset} url={url} />;
  return (
    <figure data-slot="panel-asset-unavailable" className="border-border bg-muted/30 overflow-hidden rounded-md border">
      <div className="bg-muted/50 flex aspect-4/3 items-center justify-center px-2 text-center">
        <span className="text-muted-foreground text-xs">Preview not available</span>
      </div>
      {asset.caption && (
        <figcaption className="text-muted-foreground px-2 py-1.5 text-xs leading-snug break-words">
          {asset.caption}
        </figcaption>
      )}
    </figure>
  );
}

export function GalleryBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<GalleryBlockState>) {
  const ids = useMemo(() => (Array.isArray(data.asset_ids) ? data.asset_ids : []), [data.asset_ids]);
  const refs = useMemo(() => {
    const byId = new Map((panel.state.assets ?? []).map((asset) => [asset.asset_id, asset]));
    return ids.map(
      (id): AssetRef =>
        byId.get(id) ?? { asset_id: id, kind: "image", mime: "image/*", caption: null, meta: {}, ts: 0 },
    );
  }, [ids, panel.state.assets]);
  const [enlarged, setEnlarged] = useState<string | null>(null);

  return (
    <BlockFrame spec={spec} title={title} count={refs.length} highlighted={highlighted}>
      {refs.length === 0 ? (
        <PanelEmpty>No photos yet.</PanelEmpty>
      ) : (
        <ul data-slot="block-gallery" className="grid grid-cols-2 gap-2.5">
          {refs.map((asset) => {
            const isSelected = data.selected === asset.asset_id;
            const isEnlarged = enlarged === asset.asset_id;
            return (
              <li
                key={asset.asset_id}
                data-selected={isSelected ? "true" : undefined}
                className={cn(isEnlarged && "col-span-2")}
              >
                <button
                  type="button"
                  aria-pressed={isEnlarged}
                  aria-label={`${isEnlarged ? "Shrink" : "Enlarge"} ${asset.caption ?? "photo"}`}
                  onClick={() => setEnlarged((current) => (current === asset.asset_id ? null : asset.asset_id))}
                  className={cn(
                    "focus-visible:ring-ring block w-full rounded-md text-left focus-visible:ring-2 focus-visible:outline-none",
                    isSelected && "ring-brand ring-2",
                  )}
                >
                  <FileTile asset={asset} url={panel.assets.get(asset.asset_id)} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </BlockFrame>
  );
}
