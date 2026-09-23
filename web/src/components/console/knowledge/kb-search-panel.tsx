"use client";

import * as React from "react";
import { toast } from "sonner";
import { SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { useKbSearch } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { EmptyState } from "@/components/console/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import type { KbHit } from "@/contracts/lkap-contracts";

/** Escapes a string for use inside a `RegExp`. */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Splits `text` on the words of `query` (≥ 2 chars, case-insensitive) so the
 * caller can wrap matches in `<mark>` (docs/UI_UX_SPEC.md §7.7 item 3).
 */
function highlightSegments(text: string, query: string): { value: string; match: boolean }[] {
  const terms = Array.from(new Set(query.split(/\s+/).filter((term) => term.length >= 2))).map(escapeRegExp);
  if (terms.length === 0) return [{ value: text, match: false }];
  const pattern = new RegExp(`(${terms.join("|")})`, "gi");
  return text.split(pattern).map((value, index) => ({ value, match: index % 2 === 1 }));
}

function HighlightedText({ text, query }: { text: string; query: string }) {
  const segments = highlightSegments(text, query);
  return (
    <>
      {segments.map((segment, index) =>
        segment.match ? (
          <mark key={index} className="rounded-xs bg-warning-soft text-warning-text">
            {segment.value}
          </mark>
        ) : (
          <React.Fragment key={index}>{segment.value}</React.Fragment>
        ),
      )}
    </>
  );
}

/**
 * `kb-search-panel.tsx` (docs/UI_UX_SPEC.md §7.7 item 3): "Try a question"
 * with results as cards showing file, score as a 0–100 bar, text with the
 * query terms highlighted, and an empty result state with a hint.
 */
export function KbSearchPanel({ kbId }: { kbId: string }) {
  const [query, setQuery] = React.useState("");
  const [lastQuery, setLastQuery] = React.useState("");
  const [hits, setHits] = React.useState<KbHit[] | null>(null);
  const search = useKbSearch(kbId);

  async function handleSearch(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed === "") return;
    try {
      const result = await search.mutateAsync({ query: trimmed, k: 5 });
      setHits(result.hits);
      setLastQuery(trimmed);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">Try a question</h2>
      <form onSubmit={handleSearch} className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question…"
          aria-label="Try a question"
          className="flex-1"
        />
        <Button type="submit" disabled={search.isPending || query.trim() === ""}>
          <Icon as={SearchIcon} size="sm" /> {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      {hits ? (
        hits.length === 0 ? (
          <EmptyState
            icon={SearchIcon}
            title="No matches"
            description="Try different words or upload more documents."
            compact
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {hits.map((hit) => {
              const scorePct = Math.round(Math.max(0, Math.min(1, hit.score)) * 100);
              return (
                <li key={hit.chunk_id} className="rounded-lg border border-border bg-card p-3 text-sm">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate font-medium text-foreground">{hit.filename}</span>
                    <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                      <Progress value={scorePct} className="w-16" aria-hidden="true" />
                      <span className="tabular-nums">{scorePct}</span>
                    </span>
                  </div>
                  <p className="line-clamp-4 whitespace-pre-wrap text-foreground/90">
                    <HighlightedText text={hit.text} query={lastQuery} />
                  </p>
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
}
