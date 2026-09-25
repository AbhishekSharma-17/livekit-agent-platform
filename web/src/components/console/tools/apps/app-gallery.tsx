"use client";

import * as React from "react";
import { StoreIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useToolProviderToolkits } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import { AppCard } from "@/components/console/tools/apps/app-card";
import type { ToolkitOut } from "@/contracts/lkap-contracts";

/**
 * The app gallery (docs/v5/COMPOSIO.md §6): search, a category filter (built
 * from the categories seen so far — Composio has no categories endpoint),
 * "Connected only" and cursor paging that appends ("Load more").
 */
export function AppGallery() {
  const [searchInput, setSearchInput] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState("all");
  const [connectedOnly, setConnectedOnly] = React.useState(false);

  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const toolkitsQuery = useToolProviderToolkits({
    query: search || undefined,
    category: category === "all" ? undefined : category,
    connectedOnly,
    limit: 24,
  });

  const items: ToolkitOut[] = React.useMemo(
    () => toolkitsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [toolkitsQuery.data],
  );

  const categories = React.useMemo(() => {
    const set = new Set<string>();
    for (const item of items) for (const value of item.categories ?? []) set.add(value);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [items]);

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
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="w-full sm:w-48" aria-label="Category">
            <SelectValue placeholder="All categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All categories</SelectItem>
            {categories.map((value) => (
              <SelectItem key={value} value={value}>
                {value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2">
          <Switch id="apps-gallery-connected-only" checked={connectedOnly} onCheckedChange={setConnectedOnly} aria-label="Connected only" />
          <Label htmlFor="apps-gallery-connected-only" className="text-sm font-normal">
            Connected only
          </Label>
        </div>
      </div>

      {toolkitsQuery.isLoading ? (
        <LoadingRegion label="Loading apps" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
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
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((toolkit) => (
            <AppCard key={toolkit.slug} toolkit={toolkit} />
          ))}
        </div>
      )}

      {toolkitsQuery.hasNextPage ? (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={toolkitsQuery.isFetchingNextPage}
            onClick={() => void toolkitsQuery.fetchNextPage()}
          >
            {toolkitsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
