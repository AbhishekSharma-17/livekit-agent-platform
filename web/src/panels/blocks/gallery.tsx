"use client";

/**
 * `gallery` block — images delivered on `lkap.ui.asset` (`asset_ids`), e.g.
 * every `pin_frame` photo: the worker appends each `image/*` asset to every
 * gallery block in the same patch as its `/assets` append. `selected` is
 * agent-owned (drawn with the brand ring); tapping a tile only enlarges it
 * for the viewer.
 */
import * as React from "react";
import { useMemo, useState } from "react";

import type { AssetRef, GalleryBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { AssetTile, PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

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
                  <AssetTile asset={asset} url={panel.assets.get(asset.asset_id)} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </BlockFrame>
  );
}
