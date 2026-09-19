"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useKbSearch } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { KbHit } from "@/contracts/lkap-contracts";

export function KbSearchPanel({ kbId }: { kbId: string }) {
  const [query, setQuery] = React.useState("");
  const [hits, setHits] = React.useState<KbHit[] | null>(null);
  const search = useKbSearch(kbId);

  async function handleSearch(event: React.FormEvent) {
    event.preventDefault();
    if (query.trim() === "") return;
    try {
      const result = await search.mutateAsync({ query: query.trim(), k: 4 });
      setHits(result.hits);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold">Test search</h3>
      <form onSubmit={handleSearch} className="flex gap-2">
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask a question…" className="flex-1" />
        <Button type="submit" disabled={search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      {hits ? (
        hits.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No matches.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {hits.map((hit) => (
              <div key={hit.chunk_id} className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
                <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                  <span>{hit.filename}</span>
                  <span>score {hit.score.toFixed(3)}</span>
                </div>
                <p className="line-clamp-4 whitespace-pre-wrap text-foreground/90">{hit.text}</p>
              </div>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}
