"use client";

import * as React from "react";

import { SearchField } from "@/components/shared/search-field";
import { SegmentedControl, type SegmentedOption } from "@/components/shared/segmented-control";
import { cn } from "@/lib/utils";

/**
 * Client-side list search for the build library (docs/ui/DESIGN-SYSTEM.md
 * section 9: "Search on every list once it has 6 or more items, or while a
 * query is active"). Matching is accent-insensitive and every word of the
 * query must match somewhere in the row's fields; matches are highlighted and
 * Escape clears the field (`SearchField`). A list's filter is remembered per
 * person in local storage and validated on read.
 *
 * S4 keeps this in its own tree (tools, knowledge and lookup tables share
 * it); it is a candidate to move to `components/shared` once other lists
 * adopt the same pattern.
 */

/** Lists with at least this many items get a search field. */
export const SEARCH_THRESHOLD = 6;

/** Lower-case and strip diacritics: "Café" -> "cafe". */
export function normalizeText(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** The normalized words of a query ("  Déjà vu " -> ["deja", "vu"]). */
export function queryWords(query: string): string[] {
  return normalizeText(query).split(/\s+/).filter(Boolean);
}

/** Every query word appears in at least one field (an empty query matches everything). */
export function matchesQuery(fields: ReadonlyArray<string | number | null | undefined>, query: string): boolean {
  const words = queryWords(query);
  if (words.length === 0) return true;
  const haystack = normalizeText(fields.filter((field) => field !== null && field !== undefined).join(" \u0000 "));
  return words.every((word) => haystack.includes(word));
}

/**
 * The original-text ranges `[start, end)` that match any query word,
 * accent-insensitively and merged. NFD can change a string's length, so each
 * normalized character keeps the index of the original character it came from.
 */
export function matchRanges(text: string, query: string): Array<[number, number]> {
  const words = queryWords(query);
  if (words.length === 0 || text === "") return [];
  let normalized = "";
  const origin: number[] = [];
  for (let index = 0; index < text.length; index++) {
    const piece = normalizeText(text[index]);
    // Per UTF-16 code unit, so `origin` lines up with `indexOf` below.
    for (let unit = 0; unit < piece.length; unit++) {
      normalized += piece[unit];
      origin.push(index);
    }
  }
  const ranges: Array<[number, number]> = [];
  for (const word of words) {
    let from = normalized.indexOf(word);
    while (from !== -1) {
      ranges.push([origin[from], origin[from + word.length - 1] + 1]);
      from = normalized.indexOf(word, from + word.length);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([range[0], range[1]]);
  }
  return merged;
}

/** `text` with every match of `query` wrapped in a `<mark>`. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const ranges = matchRanges(text, query);
  if (ranges.length === 0) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={index} data-slot="search-highlight" className="rounded-sm bg-warning-subtle text-foreground">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private mode or a full quota: the filter just isn't remembered.
  }
}

/**
 * A string state remembered in local storage under `key`. The stored value is
 * validated against `allowed` on read and falls back to `fallback` when it is
 * missing or unknown. Read after mount, so server and first client render agree.
 */
export function useRememberedChoice<T extends string>(key: string, allowed: readonly T[], fallback: T): [T, (next: T) => void] {
  const [value, setValue] = React.useState<T>(fallback);
  const allowedKey = allowed.join("|");
  React.useEffect(() => {
    const stored = readStored(key);
    if (stored !== null && (allowedKey.split("|") as string[]).includes(stored)) setValue(stored as T);
  }, [key, allowedKey]);
  const update = React.useCallback(
    (next: T) => {
      setValue(next);
      writeStored(key, next);
    },
    [key],
  );
  return [value, update];
}

export interface ListToolbarProps<T extends string> {
  /** Plural noun for the search label: "tools" -> "Search tools". */
  items: string;
  query: string;
  onQueryChange: (query: string) => void;
  placeholder?: string;
  /** Optional segmented status filter. */
  filter?: {
    label: string;
    value: T;
    onValueChange: (value: T) => void;
    options: SegmentedOption<T>[];
  };
  className?: string;
}

/**
 * The search row above a library list: a `SearchField` and, optionally, a
 * segmented filter with counts. Wraps on phones; the segmented control
 * scrolls sideways rather than overflowing.
 */
export function ListToolbar<T extends string>({ items, query, onQueryChange, placeholder, filter, className }: ListToolbarProps<T>) {
  return (
    <div data-slot="list-toolbar" className={cn("mb-4 flex flex-wrap items-center gap-3", className)}>
      <SearchField
        value={query}
        onValueChange={onQueryChange}
        aria-label={`Search ${items}`}
        placeholder={placeholder ?? `Search ${items}…`}
        wrapperClassName="w-full sm:w-72 sm:max-w-72"
      />
      {filter ? (
        <SegmentedControl label={filter.label} value={filter.value} onValueChange={filter.onValueChange} options={filter.options} />
      ) : null}
    </div>
  );
}
