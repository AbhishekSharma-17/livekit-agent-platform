"use client";

import * as React from "react";

import { NoMatches } from "@/components/shared/empty-state";
import { SearchField } from "@/components/shared/search-field";
import { SegmentedControl, type SegmentedOption } from "@/components/shared/segmented-control";
import { cn } from "@/lib/utils";

/**
 * The one list search (docs/ui/DESIGN-SYSTEM.md section 9), for every list
 * screen: shown once a list has 6 or more items (or while a query is active),
 * client-side, accent-insensitive, every word must match somewhere, matches
 * highlighted, Escape clears (`SearchField`), and a list's query and filters
 * are remembered per person in `localStorage`, validated on read.
 *
 * - `useListSearch` + `ListSearchField` + `ListNoMatches`: a remembered query
 *   over a list's rows (settings, telephony, connect, observe).
 * - `ListToolbar`: the search field plus an optional segmented filter, for a
 *   list that owns its query state (the build library).
 * - `matchesQuery`, `matchRanges`, `Highlight`: the matching itself.
 * - `useRememberedQuery`, `useRememberedChoice`, `readStoredFilters`,
 *   `writeStoredFilters`: the remembered state.
 *
 * Remembered values live under `lkap:list:<listId>`. A value saved under the
 * settings lists' old `lkap:settings:search:<listId>` key (or a caller's own
 * `legacyKeys`) is read once, moved to the new key and removed.
 */
export const SEARCH_THRESHOLD = 6;

const MAX_QUERY_LENGTH = 200;
export const LIST_STORAGE_PREFIX = "lkap:list:";
const LEGACY_STORAGE_PREFIX = "lkap:settings:search:";

/** Lower-case and accent-insensitive: "é" matches "e". */
export function normalizeText(text: string): string {
  return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** The normalised words of a query ("  Déjà vu " -> ["deja", "vu"]); a blank query has none. */
export function queryWords(query: string): string[] {
  return normalizeText(query).trim().split(/\s+/).filter(Boolean);
}

/** Whether every word of `query` appears in at least one of `fields`. An empty query matches everything. */
export function matchesQuery(fields: ReadonlyArray<string | number | null | undefined>, query: string): boolean {
  const words = queryWords(query);
  if (words.length === 0) return true;
  const haystack = fields
    .filter((field): field is string | number => typeof field === "number" || (typeof field === "string" && field.length > 0))
    .map((field) => normalizeText(String(field)))
    .join("\n");
  return words.every((word) => haystack.includes(word));
}

/**
 * The normalised form of `text` plus, for each normalised character, the index
 * of the original character it came from — so a match found in the normalised
 * string maps back onto the original one ("Zoë" is 3 characters, its NFD form 4).
 */
function normalizedWithMap(text: string): { normalized: string; origin: number[] } {
  let normalized = "";
  const origin: number[] = [];
  let index = 0;
  for (const char of text) {
    const folded = normalizeText(char);
    for (let i = 0; i < folded.length; i += 1) origin.push(index);
    normalized += folded;
    index += char.length;
  }
  return { normalized, origin };
}

/** Original-string ranges `[start, end)` covered by any query word, merged. */
export function matchRanges(text: string, query: string): Array<[number, number]> {
  const words = queryWords(query);
  if (words.length === 0 || text.length === 0) return [];
  const { normalized, origin } = normalizedWithMap(text);
  const ranges: Array<[number, number]> = [];
  for (const word of words) {
    let from = normalized.indexOf(word);
    while (from !== -1) {
      const lastIndex = from + word.length - 1;
      const start = origin[from];
      const lastOrigin = origin[lastIndex];
      const end = lastOrigin + (text.codePointAt(lastOrigin)! > 0xffff ? 2 : 1);
      ranges.push([start, end]);
      from = normalized.indexOf(word, from + word.length);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

/**
 * `text` with every query match wrapped in a `<mark>`: foreground on
 * `--brand-subtle`, a pair `pnpm check:contrast` holds at 4.5:1 in both
 * themes. Plain text when there is no query; wrapped in a `<span>` only when
 * given a `className`.
 */
export function Highlight({ text, query, className }: { text: string; query: string; className?: string }) {
  const ranges = matchRanges(text, query);
  const wrap = (content: React.ReactNode) => (className ? <span className={className}>{content}</span> : <>{content}</>);
  if (ranges.length === 0) return wrap(text);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={index} data-slot="search-highlight" className="rounded-sm bg-brand-subtle text-foreground">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return wrap(parts);
}

/** A list's storage key: `lkap:list:<listId>`. */
export function listStorageKey(listId: string): string {
  return `${LIST_STORAGE_PREFIX}${listId}`;
}

function rawGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function rawRemove(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Blocked storage: nothing to clean up.
  }
}

function writeStored(key: string, value: string) {
  try {
    if (value === "") window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value.slice(0, MAX_QUERY_LENGTH));
  } catch {
    // Private mode or blocked storage: the search still works, it just isn't remembered.
  }
}

/** Validated on read: a string of sane length, nothing else. */
function validString(value: string | null): string {
  return typeof value === "string" && value.length <= MAX_QUERY_LENGTH ? value : "";
}

/**
 * The value remembered for `listId`. While the new key holds nothing, the
 * first legacy key that does is migrated once: its value moves to the new key
 * (when it passes validation) and the legacy key is removed.
 */
function readStored(listId: string, legacyKeys: ReadonlyArray<string>): string {
  const key = listStorageKey(listId);
  const current = rawGet(key);
  if (current !== null) return validString(current);
  for (const legacy of [`${LEGACY_STORAGE_PREFIX}${listId}`, ...legacyKeys]) {
    const value = rawGet(legacy);
    if (value === null) continue;
    rawRemove(legacy);
    const valid = validString(value);
    if (valid) writeStored(key, valid);
    return valid;
  }
  return "";
}

export interface RememberedOptions {
  /** Older storage keys to migrate from (read once, then removed). */
  legacyKeys?: ReadonlyArray<string>;
}

/** A stable dependency for an options array that callers usually write inline. */
function useLegacyKeys(options: RememberedOptions): ReadonlyArray<string> {
  const joined = (options.legacyKeys ?? []).join("\u0000");
  return React.useMemo(() => (joined ? joined.split("\u0000") : []), [joined]);
}

/**
 * A list's search query, remembered per list in `localStorage`. Read after
 * mount (never during render), so server and client markup agree.
 */
export function useRememberedQuery(listId: string, options: RememberedOptions = {}): [string, (value: string) => void] {
  const key = listStorageKey(listId);
  const legacyKeys = useLegacyKeys(options);
  const [query, setQuery] = React.useState("");
  React.useEffect(() => {
    const stored = readStored(listId, legacyKeys);
    if (stored) setQuery(stored);
  }, [listId, legacyKeys]);
  const update = React.useCallback(
    (value: string) => {
      setQuery(value);
      writeStored(key, value);
    },
    [key],
  );
  return [query, update];
}

/**
 * A remembered choice from a fixed set (e.g. a filter's value), validated on
 * read against `allowed`: a stale id falls back to `fallback`.
 */
export function useRememberedChoice<T extends string>(
  listId: string,
  fallback: T,
  allowed: ReadonlyArray<T>,
  options: RememberedOptions = {},
): [T, (value: T) => void] {
  const key = listStorageKey(listId);
  const legacyKeys = useLegacyKeys(options);
  const [stored, setStored] = React.useState<string>(fallback);
  React.useEffect(() => {
    const value = readStored(listId, legacyKeys);
    if (value) setStored(value);
  }, [listId, legacyKeys]);
  const update = React.useCallback(
    (value: T) => {
      setStored(value);
      writeStored(key, value === fallback ? "" : value);
    },
    [key, fallback],
  );
  const value = stored === fallback || (allowed as ReadonlyArray<string>).includes(stored) ? (stored as T) : fallback;
  return [value, update];
}

/**
 * Several of a list's filters at once, as JSON under an explicit key,
 * validated on read: `parse` returns `null` for anything it doesn't
 * recognise, and a storage error (private mode, quota) reads as "nothing
 * saved".
 */
export function readStoredFilters<T>(key: string, parse: (raw: unknown) => T | null): T | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    return parse(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function writeStoredFilters(key: string, value: unknown): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage is a convenience; a failed write only forgets the filters.
  }
}

export interface ListSearchState<T> {
  query: string;
  setQuery: (value: string) => void;
  /** Rows matching the query (all rows while it's empty). */
  filtered: T[];
  /** Whether to render the search field: 6+ items, or a query is active. */
  showSearch: boolean;
  /** A query is active and nothing matches it. */
  noMatches: boolean;
  clear: () => void;
}

/** Filter `rows` by a remembered query over the text `fields` returns for each row. */
export function useListSearch<T>(
  listId: string,
  rows: T[],
  fields: (row: T) => ReadonlyArray<string | number | null | undefined>,
): ListSearchState<T> {
  const [query, setQuery] = useRememberedQuery(listId);
  const filtered = React.useMemo(
    () => (query.trim() === "" ? rows : rows.filter((row) => matchesQuery(fields(row), query))),
    // `fields` is a per-render closure; the rows and query decide the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, query],
  );
  return {
    query,
    setQuery,
    filtered,
    showSearch: rows.length >= SEARCH_THRESHOLD || query.trim() !== "",
    noMatches: query.trim() !== "" && filtered.length === 0 && rows.length > 0,
    clear: () => setQuery(""),
  };
}

/** The search field above a list, with a polite count of what's shown. */
export function ListSearchField({
  search,
  label,
  total,
  className,
}: {
  search: ListSearchState<unknown>;
  /** "Search members" — the field has no visible label. */
  label: string;
  total: number;
  className?: string;
}) {
  if (!search.showSearch) return null;
  const active = search.query.trim() !== "";
  return (
    <div className={cn("mb-3 flex flex-wrap items-center gap-x-3 gap-y-2", className)}>
      <SearchField value={search.query} onValueChange={search.setQuery} aria-label={label} placeholder={`${label}…`} />
      <p role="status" aria-live="polite" className="text-caption text-text-secondary tabular-nums">
        {active ? `${search.filtered.length} of ${total}` : ""}
      </p>
    </div>
  );
}

/** "No {items} match “{query}”" with Clear filters. */
export function ListNoMatches({ search, items }: { search: ListSearchState<unknown>; items: string }) {
  return <NoMatches items={items} query={search.query} onClear={search.clear} />;
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
 * The search row above a list that owns its query state: a `SearchField` and,
 * optionally, a segmented filter with counts. Wraps on phones; the segmented
 * control scrolls sideways rather than overflowing.
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
