"use client";

import * as React from "react";
import { StoreIcon } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchableSelect, type SearchableSelectOption } from "@/components/ui/searchable-select";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useToolProviderCategories, useToolProviderToolkits } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import { AppCard } from "@/components/console/tools/apps/app-card";
import type { ToolkitOut } from "@/contracts/lkap-contracts";

/** How long typing pauses before the search actually reaches the api. */
const SEARCH_DEBOUNCE_MS = 250;

/** Placeholder cards while the first page loads, sized like a real `AppCard` so nothing jumps once it arrives. */
const SKELETON_COUNT = 6;

/** "developer-tools" -> "Developer tools"; only used when Composio sends no display `name` for a category. */
function sentenceCase(text: string): string {
  return text.length === 0 ? text : text[0].toUpperCase() + text.slice(1);
}

function humanizeCategoryId(id: string): string {
  return sentenceCase(id.replace(/[-_]+/g, " "));
}

/**
 * The app gallery (docs/v5/COMPOSIO.md §6): search, a category filter (the
 * complete list from `GET .../categories`, not just the categories seen
 * among the apps loaded so far), "Connected only" and cursor paging that
 * appends ("Load more" — also prefetched on hover/focus and near-scroll, so
 * the click usually lands on already-fetched data).
 */
export function AppGallery() {
  const [searchInput, setSearchInput] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState("all");
  const [connectedOnly, setConnectedOnly] = React.useState(false);

  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const toolkitsQuery = useToolProviderToolkits({
    query: search || undefined,
    category: category === "all" ? undefined : category,
    connectedOnly,
    limit: 24,
  });
  const categoriesQuery = useToolProviderCategories();

  const items: ToolkitOut[] = React.useMemo(
    () => toolkitsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [toolkitsQuery.data],
  );

  const categoryOptions: SearchableSelectOption[] = React.useMemo(() => {
    const raw = categoriesQuery.data?.items ?? [];
    return raw
      .map((cat) => ({ value: cat.id, label: cat.name ? sentenceCase(cat.name) : humanizeCategoryId(cat.id) }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [categoriesQuery.data]);

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = toolkitsQuery;
  const prefetchNext = React.useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  // Prefetch the next page a little before the user reaches the bottom, so
  // "Load more" usually lands on data that's already there; the button stays
  // as the explicit, always-available action (and jsdom/older browsers
  // without `IntersectionObserver` just keep working with the button alone).
  const sentinelRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const node = sentinelRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) prefetchNext();
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [prefetchNext]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search apps"
          aria-label="Search apps"
          className="w-full sm:w-64"
        />
        <SearchableSelect
          value={category}
          onValueChange={setCategory}
          allOption={{ value: "all", label: "All categories" }}
          options={categoryOptions}
          loading={categoriesQuery.isLoading}
          loadingText="Loading categories…"
          searchPlaceholder="Search categories…"
          emptyText="No categories match."
          aria-label="Category"
          triggerClassName="w-full sm:w-48"
        />
        <div className="flex items-center gap-2">
          <Switch id="apps-gallery-connected-only" checked={connectedOnly} onCheckedChange={setConnectedOnly} aria-label="Connected only" />
          <Label htmlFor="apps-gallery-connected-only" className="text-sm font-normal">
            Connected only
          </Label>
        </div>
      </div>

      {toolkitsQuery.isLoading ? (
        <LoadingRegion label="Loading apps" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: SKELETON_COUNT }, (_, i) => (
            <AppCardSkeleton key={i} />
          ))}
        </LoadingRegion>
      ) : toolkitsQuery.isError ? (
        <ErrorBanner message={`Couldn't load apps — ${appsErrorMessage(toolkitsQuery.error)}`} onRetry={() => toolkitsQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={StoreIcon}
          title="No apps match"
          description={connectedOnly ? "Nothing is connected yet." : "Try a different search or category."}
          compact
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((toolkit) => (
              <AppCard key={toolkit.slug} toolkit={toolkit} />
            ))}
          </div>
          {/* An empty, unrendered probe just above "Load more" — not itself the trigger. */}
          <div ref={sentinelRef} aria-hidden="true" />
        </>
      )}

      {toolkitsQuery.hasNextPage ? (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={toolkitsQuery.isFetchingNextPage}
            onMouseEnter={prefetchNext}
            onFocus={prefetchNext}
            onClick={() => void toolkitsQuery.fetchNextPage()}
          >
            {toolkitsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Placeholder matching `AppCard`'s real shape (logo, name, description, category chips, action) — no layout jump when data lands. */
function AppCardSkeleton() {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <Skeleton className="size-8 shrink-0 rounded-sm" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <div className="flex gap-1">
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
        </div>
      </div>
      <Skeleton className="h-7 w-20" />
    </div>
  );
}
