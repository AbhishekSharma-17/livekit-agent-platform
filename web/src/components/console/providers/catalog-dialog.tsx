"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { StatusChip } from "@/components/shared/status-chip";
import { useCatalog, useRefreshCatalog, type CatalogKind } from "@/hooks/useCatalog";
import type { ProviderOut } from "@/contracts/lkap-contracts";
import { SkeletonRows } from "@/components/shared/loading-state";

/**
 * Providers catalog's "Catalog" button (UI_UX_SPEC-V2-AMENDMENTS §2.2):
 * previews models/voices/avatars from the vendor with the selected key.
 * Reuses the same image-key sniffing as the avatar picker
 * (`registry-form.tsx`'s `previewImageUrl` — duplicated here rather than
 * exported, since the two live in different directories and the heuristic
 * is a few lines).
 */
const IMAGE_META_KEYS = ["image_url", "imageUrl", "thumbnail_url", "thumbnailUrl", "preview_url", "previewUrl", "avatar_url", "avatarUrl", "photo_url", "photoUrl", "picture", "thumbnail", "image"] as const;

function previewImageUrl(meta: Record<string, unknown> | undefined): string | null {
  if (!meta) return null;
  for (const key of IMAGE_META_KEYS) {
    const value = meta[key];
    if (typeof value === "string" && /^https?:\/\//.test(value)) return value;
  }
  return null;
}

function firstCatalogKind(provider: ProviderOut): CatalogKind | null {
  return provider.catalog?.kinds?.[0] ?? null;
}

export function CatalogDialog({ provider }: { provider: ProviderOut }) {
  const kind = firstCatalogKind(provider);
  const [open, setOpen] = React.useState(false);
  const { data, isLoading, isError } = useCatalog(provider.id, kind ?? "models", provider.default_credential_id, {
    enabled: open && Boolean(kind),
  });
  const refresh = useRefreshCatalog(provider.id, kind ?? "models", provider.default_credential_id);

  if (!provider.catalog || !kind) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          Catalog
        </Button>
      </DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{provider.label} catalog</DialogTitle>
          <DialogDescription>
            {provider.default_credential_id
              ? "Previewed with this provider's default key."
              : "No default key set — showing the static/cached list."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-2">
          {isLoading || refresh.isPending ? (
            <SkeletonRows label="Loading the catalog" rows={4} rowClassName="h-12" />
          ) : isError ? (
            <p className="text-sm text-danger-text">Couldn&apos;t load the catalog.</p>
          ) : (data?.items ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No items yet.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {(refresh.data?.items ?? data?.items ?? []).map((item) => {
                const preview = previewImageUrl(item.meta);
                return (
                  <li key={item.id} className="flex items-center gap-2.5 rounded-md border border-border px-3 py-2">
                    {preview ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={preview} alt="" className="size-8 shrink-0 rounded-full border border-border object-cover" />
                    ) : null}
                    <div className="min-w-0">
                      <p className="truncate text-sm text-foreground" title={item.label}>{item.label}</p>
                      <p className="truncate font-mono text-xs text-muted-foreground" title={item.id}>{item.id}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {data?.error ? (
            <p className="text-xs text-muted-foreground">
              <StatusChip tone="neutral" size="sm">
                Fallback
              </StatusChip>{" "}
              {data.error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? "Refreshing…" : "Refresh"}
          </Button>
          <Button type="button" onClick={() => setOpen(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
