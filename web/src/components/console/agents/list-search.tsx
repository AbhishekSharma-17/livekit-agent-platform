import * as React from "react";

/**
 * Client-side list search (docs/ui/DESIGN-SYSTEM.md section 9): matching is
 * accent-insensitive and case-insensitive, every word of the query must match
 * somewhere, and the matched text is highlighted.
 */

const MARKS = /\p{M}+/gu;

/** "Café Désk" → "cafe desk". */
export function foldText(value: string): string {
  return value.normalize("NFD").replace(MARKS, "").toLowerCase();
}

/** The query's words, folded; an empty or blank query has none. */
export function queryWords(query: string): string[] {
  return foldText(query).split(/\s+/).filter(Boolean);
}

/** True when every word appears in at least one field (no words: everything matches). */
export function matchesAllWords(words: readonly string[], fields: readonly (string | null | undefined)[]): boolean {
  if (words.length === 0) return true;
  const folded = fields.filter((field): field is string => Boolean(field)).map(foldText);
  return words.every((word) => folded.some((field) => field.includes(word)));
}

/**
 * Folds `text` one character at a time, keeping a map from each folded
 * character back to its source index, so a match in the folded string can be
 * highlighted in the original.
 */
function foldWithMap(text: string): { folded: string; source: number[] } {
  let folded = "";
  const source: number[] = [];
  for (let index = 0; index < text.length; index++) {
    const piece = foldText(text[index]);
    folded += piece;
    for (let k = 0; k < piece.length; k++) source.push(index);
  }
  return { folded, source };
}

/** Source ranges `[start, end)` of every word's occurrences, merged. */
export function matchRanges(text: string, words: readonly string[]): Array<[number, number]> {
  if (words.length === 0 || !text) return [];
  const { folded, source } = foldWithMap(text);
  const ranges: Array<[number, number]> = [];
  for (const word of words) {
    let from = 0;
    for (;;) {
      const at = folded.indexOf(word, from);
      if (at === -1) break;
      ranges.push([source[at], source[at + word.length - 1] + 1]);
      from = at + word.length;
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

/** `text` with the query's matches wrapped in `<mark>`. */
export function Highlight({ text, words }: { text: string; words: readonly string[] }) {
  const ranges = matchRanges(text, words);
  if (ranges.length === 0) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={index} data-slot="search-match" className="rounded-sm bg-brand-subtle text-foreground">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

/**
 * A per-person list preference in localStorage, validated on read: `parse`
 * returns `null` for anything it doesn't recognise, and a storage error
 * (private mode, quota) reads as "nothing saved".
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
