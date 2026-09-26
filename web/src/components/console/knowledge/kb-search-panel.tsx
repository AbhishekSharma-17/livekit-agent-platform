"use client";

import * as React from "react";
import { toast } from "sonner";
import { SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useKbSearch } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { EmptyState } from "@/components/console/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { cn } from "@/lib/utils";
import type { KbHit, KbSearchRequest } from "@/contracts/lkap-contracts";

/** `KbSearchOptions.mode`/`.rerank` aren't exported as standalone unions (inlined into every request/response model); pull them off `KbSearchRequest` instead of re-declaring the literals by hand. */
type KbSearchMode = NonNullable<KbSearchRequest["mode"]>;
type KbRerankMode = NonNullable<KbSearchRequest["rerank"]>;

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

/** `hit.meta`'s locators (V5-01 ingest), e.g. "page 3 · Deductibles › Wind and hail". */
function locatorLine(hit: KbHit): string | null {
  const meta = hit.meta ?? {};
  const parts: string[] = [];
  const page = meta["page"];
  if (typeof page === "number") parts.push(`page ${page}`);
  const headingPath = meta["heading_path"];
  if (Array.isArray(headingPath) && headingPath.length > 0) {
    parts.push(headingPath.filter((part): part is string => typeof part === "string").join(" › "));
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

interface SearchColumn {
  key: "vector" | "hybrid" | "vector_rerank" | "hybrid_rerank";
  label: string;
  hint: string;
  mode: KbSearchMode;
  rerank: KbRerankMode;
}

/**
 * The four ways a turn can be retrieved (docs/v5/PLAN-V5.md V5-10): plain
 * embedding similarity, hybrid (also matching exact words), and each with the
 * local re-ranker's second pass. Run side by side against the same question
 * so a builder can see which setting actually helps before changing the
 * agent's Knowledge tab.
 */
const COLUMNS: SearchColumn[] = [
  { key: "vector", label: "Vector", hint: "Matches by meaning only.", mode: "vector", rerank: "none" },
  { key: "hybrid", label: "Hybrid", hint: "Also matches exact words.", mode: "hybrid", rerank: "none" },
  {
    key: "vector_rerank",
    label: "Vector + re-ranked",
    hint: "Vector matches, double-checked by the re-ranker.",
    mode: "vector",
    rerank: "local",
  },
  {
    key: "hybrid_rerank",
    label: "Hybrid + re-ranked",
    hint: "Hybrid matches, double-checked by the re-ranker.",
    mode: "hybrid",
    rerank: "local",
  },
];

const RESULTS_PER_COLUMN = 5;

/**
 * `kb-search-panel.tsx` (docs/v5/PLAN-V5.md V5-10, docs/UI_UX_SPEC.md §7.7
 * item 3): one question run four ways at once — vector, hybrid, and each
 * with the local re-ranker — each column showing scores and locators (page,
 * heading path) so a builder can see which retrieval setting actually finds
 * the right passage before changing it on the agent's Knowledge tab. Each
 * column is its own `POST .../search` call (the api has no combined route),
 * so one column failing (a warning, a timeout) never blanks the others.
 */
export function KbSearchPanel({ kbId }: { kbId: string }) {
  const [query, setQuery] = React.useState("");
  const [lastQuery, setLastQuery] = React.useState("");
  const [results, setResults] = React.useState<Record<SearchColumn["key"], KbHit[]> | null>(null);
  const [columnErrors, setColumnErrors] = React.useState<Partial<Record<SearchColumn["key"], string>>>({});
  const [pending, setPending] = React.useState(false);

  // Independent mutations, one per column, so each request carries its own
  // {mode, rerank} and a failure in one never touches the others' state.
  const vector = useKbSearch(kbId);
  const hybrid = useKbSearch(kbId);
  const vectorRerank = useKbSearch(kbId);
  const hybridRerank = useKbSearch(kbId);
  const mutationByColumn = React.useMemo(
    () => ({ vector, hybrid, vector_rerank: vectorRerank, hybrid_rerank: hybridRerank }),
    [vector, hybrid, vectorRerank, hybridRerank],
  );

  async function handleSearch(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed === "") return;
    setPending(true);
    const settled = await Promise.allSettled(
      COLUMNS.map((column) =>
        mutationByColumn[column.key].mutateAsync({
          query: trimmed,
          k: RESULTS_PER_COLUMN,
          mode: column.mode,
          rerank: column.rerank,
        }),
      ),
    );
    const nextResults = {} as Record<SearchColumn["key"], KbHit[]>;
    const nextErrors: Partial<Record<SearchColumn["key"], string>> = {};
    let anyOk = false;
    settled.forEach((outcome, index) => {
      const key = COLUMNS[index].key;
      if (outcome.status === "fulfilled") {
        const warnings = outcome.value.warnings ?? [];
        nextResults[key] = outcome.value.hits;
        if (warnings.length > 0) nextErrors[key] = warnings[0].message;
        anyOk = true;
      } else {
        nextResults[key] = [];
        nextErrors[key] = errorMessage(outcome.reason);
      }
    });
    setPending(false);
    setResults(nextResults);
    setColumnErrors(nextErrors);
    setLastQuery(trimmed);
    if (!anyOk) toast.error("Couldn't run the test search.");
  }

  const totalHits = results ? Object.values(results).reduce((sum, hits) => sum + hits.length, 0) : 0;

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">Test search</h2>
      <p className="text-[0.8125rem] text-muted-foreground">
        Runs one question four ways at once, so you can see which setting finds the right passage.
      </p>
      <form onSubmit={handleSearch} className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question…"
          aria-label="Try a question"
          className="flex-1"
        />
        <Button type="submit" disabled={pending || query.trim() === ""}>
          <Icon as={SearchIcon} size="sm" /> {pending ? "Searching…" : "Search"}
        </Button>
      </form>

      {results ? (
        totalHits === 0 ? (
          <EmptyState
            icon={SearchIcon}
            title="No matches"
            description="Try different words or upload more documents."
            compact
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {COLUMNS.map((column) => {
              const hits = results[column.key] ?? [];
              const warning = columnErrors[column.key];
              return (
                <div
                  key={column.key}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-3"
                >
                  <div>
                    <h3 className="text-sm font-semibold text-foreground">{column.label}</h3>
                    <p className="text-[0.6875rem] text-muted-foreground">{column.hint}</p>
                  </div>
                  {warning ? <p className="text-[0.6875rem] text-warning-text">{warning}</p> : null}
                  {hits.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No matches.</p>
                  ) : (
                    <ul className="flex flex-col gap-2">
                      {hits.map((hit, index) => {
                        const scorePct = Math.round(Math.max(0, Math.min(1, hit.score)) * 100);
                        const locator = locatorLine(hit);
                        return (
                          <li
                            key={hit.chunk_id}
                            className={cn(
                              "rounded-md border p-2 text-xs",
                              index === 0 ? "border-primary/50 bg-primary/5" : "border-border",
                            )}
                          >
                            <div className="mb-1 flex items-center justify-between gap-2">
                              <span className="min-w-0 truncate font-medium text-foreground">{hit.filename}</span>
                              <span className="shrink-0 tabular-nums text-muted-foreground">{scorePct}%</span>
                            </div>
                            {locator ? <p className="mb-1 text-muted-foreground">{locator}</p> : null}
                            {index === 0 ? (
                              <span className="mb-1 inline-block rounded-xs bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium text-brand-text">
                                Top match
                              </span>
                            ) : null}
                            <p className="line-clamp-3 whitespace-pre-wrap text-foreground/90">
                              <HighlightedText text={hit.text} query={lastQuery} />
                            </p>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )
      ) : null}
    </div>
  );
}
