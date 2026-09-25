"use client";

import * as React from "react";
import { ChevronLeftIcon, ChevronRightIcon, FlaskConicalIcon, SearchIcon } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { StatusChip } from "@/components/shared/status-chip";
import { SkeletonRows } from "@/components/shared/loading-state";
import { useCredentials, useProviderModels, useTestModel } from "@/components/console/lib/api-hooks";
import { useWriteAccess } from "@/components/console/lib/roles";
import { TestedChip, testedStateFor, testErrorMessage } from "@/components/console/registry/model-test-panel";
import { CATALOG_PAGE_SIZE, useCatalog, useRefreshCatalog, type CatalogKind, type CatalogParams } from "@/hooks/useCatalog";
import { isSendableModelId } from "@/lib/model-ids";
import type { CatalogItem, ModelTestResult, ProviderModelOut, ProviderOut } from "@/contracts/lkap-contracts";

/**
 * Providers catalog's "Catalog" button (UI_UX_SPEC-V2-AMENDMENTS §2.2;
 * V4-09): the vendor's models/voices/avatars with the provider's default key,
 * searched and paged by the api (200 per page, `total` from the api), and —
 * for a model list whose provider has a test — the tested chip and a Test
 * button per row (admins; the one call spends vendor money, so it stays an
 * explicit click). Reuses the same image-key sniffing as the avatar picker
 * (`registry-form.tsx`'s `previewImageUrl` — duplicated here rather than
 * exported, since the two live in different directories and the heuristic is
 * a few lines).
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

/** Waits `ms` after the last change (the search box asks the api's cache, not the vendor). */
function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

const MODEL_KINDS = new Set(["realtime", "stt", "llm", "tts", "image_gen", "embedding"]);

export function CatalogDialog({ provider }: { provider: ProviderOut }) {
  const kind = firstCatalogKind(provider);
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const q = useDebounced(search.trim());
  const params: CatalogParams = { q: q || undefined, offset, limit: CATALOG_PAGE_SIZE };
  const { data, isLoading, isError, isFetching } = useCatalog(provider.id, kind ?? "models", provider.default_credential_id, {
    enabled: open && Boolean(kind),
    params,
  });
  const refresh = useRefreshCatalog(provider.id, kind ?? "models", provider.default_credential_id, params);

  // A new search starts on page one.
  React.useEffect(() => setOffset(0), [q]);

  const testable = kind === "models" && Boolean(provider.probe) && MODEL_KINDS.has(provider.kind);
  const records = useProviderModels(provider.id, { limit: 200 }, { enabled: open && testable });
  const credentials = useCredentials();
  const fingerprint =
    provider.requires_credential === false
      ? null
      : provider.default_credential_id
        ? credentials.data?.items.find((c) => c.id === provider.default_credential_id)?.fingerprint
        : undefined;
  const recordById = React.useMemo(() => {
    const map = new Map<string, ProviderModelOut>();
    for (const record of records.data?.items ?? []) map.set(record.model_id, record);
    return map;
  }, [records.data]);
  const { canWrite: isAdmin } = useWriteAccess("admin");

  if (!provider.catalog || !kind) return null;

  const page = data;
  const items = page?.items ?? [];
  const total = page?.total ?? items.length;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + items.length, total);
  const busy = isLoading || refresh.isPending;
  const searchId = `catalog-search-${provider.id}`;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setSearch("");
          setOffset(0);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          Catalog
        </Button>
      </DialogTrigger>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{provider.label} catalog</DialogTitle>
          <DialogDescription>
            {provider.default_credential_id
              ? "Listed with this provider's default key."
              : "No default key set — showing the saved or built-in list."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-3">
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input
              id={searchId}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={`Search ${kind} by name or id`}
              aria-label={`Search the ${kind}`}
              className="pl-8"
            />
          </div>
          <p className="text-xs text-muted-foreground tabular-nums" aria-live="polite" data-testid="catalog-range">
            {busy ? "Loading…" : total === 0 ? (q ? "Nothing matches." : "") : `Showing ${from}–${to} of ${total}`}
          </p>
          {busy ? (
            <SkeletonRows label="Loading the catalog" rows={4} rowClassName="h-12" />
          ) : isError ? (
            <p className="text-sm text-danger-text">Couldn&apos;t load the catalog.</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">{q ? "No items match this search." : "No items yet."}</p>
          ) : (
            <ul className="flex flex-col gap-1.5" aria-busy={isFetching}>
              {items.map((item) => (
                <CatalogRow
                  key={item.id}
                  item={item}
                  provider={provider}
                  testable={testable}
                  canTest={isAdmin}
                  record={recordById.get(item.id)}
                  fingerprint={fingerprint}
                />
              ))}
            </ul>
          )}
          {page?.error ? (
            <p className="text-xs text-muted-foreground">
              <StatusChip tone="neutral" size="sm">
                Fallback
              </StatusChip>{" "}
              {page.error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-row flex-wrap items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setOffset((o) => Math.max(0, o - CATALOG_PAGE_SIZE))}
              disabled={offset === 0 || busy}
            >
              <ChevronLeftIcon aria-hidden="true" />
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setOffset((o) => o + CATALOG_PAGE_SIZE)}
              disabled={offset + CATALOG_PAGE_SIZE >= total || busy}
            >
              Next
              <ChevronRightIcon aria-hidden="true" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              {refresh.isPending ? "Refreshing…" : "Refresh"}
            </Button>
            <Button type="button" size="sm" onClick={() => setOpen(false)}>
              Close
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CatalogRow({
  item,
  provider,
  testable,
  canTest,
  record,
  fingerprint,
}: {
  item: CatalogItem;
  provider: ProviderOut;
  testable: boolean;
  canTest: boolean;
  record: ProviderModelOut | undefined;
  fingerprint: string | null | undefined;
}) {
  const preview = previewImageUrl(item.meta);
  const test = useTestModel(provider.id);
  const [result, setResult] = React.useState<ModelTestResult | null>(null);
  const sendable = isSendableModelId(item.id);
  const state = result
    ? testedStateFor({
        spec: provider,
        record: {
          ...(record ?? ({} as ProviderModelOut)),
          model_id: item.id,
          last_test_at: result.checked_at,
          last_test_ok: result.ok ?? null,
          last_test_message: result.message ?? null,
          last_test_fingerprint: fingerprint ?? null,
        },
        fingerprint,
      })
    : testedStateFor({ spec: provider, record, fingerprint });
  const showChip = testable && (state.kind === "ok" || state.kind === "failed");

  return (
    <li className="flex flex-col gap-1.5 rounded-md border border-border px-3 py-2" data-catalog-id={item.id}>
      <div className="flex min-w-0 items-center gap-2.5">
        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={preview} alt="" className="size-8 shrink-0 rounded-full border border-border object-cover" />
        ) : null}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-foreground" title={item.label}>
            {item.label}
          </p>
          <p className="truncate font-mono text-xs text-muted-foreground" title={item.id}>
            {item.id}
          </p>
        </div>
        {testable && canTest && sendable ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={test.isPending}
            onClick={() =>
              test.mutate(
                { model: item.id, credential_id: provider.default_credential_id ?? null, fields: {}, probes: ["basic", "tools"] },
                { onSuccess: setResult },
              )
            }
          >
            <FlaskConicalIcon aria-hidden="true" />
            {test.isPending ? "Testing…" : "Test"}
            <span className="sr-only"> {item.label}</span>
          </Button>
        ) : null}
      </div>
      {showChip || test.isError || (result && typeof result.latency_ms === "number") ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
          {showChip ? <TestedChip state={state} className="max-w-full" /> : null}
          {result && typeof result.latency_ms === "number" ? <span className="tabular-nums">{result.latency_ms} ms</span> : null}
          {test.isError ? <span className="text-danger-text">{testErrorMessage(test.error)}</span> : null}
        </div>
      ) : null}
    </li>
  );
}
