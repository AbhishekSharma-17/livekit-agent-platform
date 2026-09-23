/**
 * Flattens a `jsondiffpatch` delta into rows the version history dialog lists
 * (V2-16). Pure: the history dialog imports `jsondiffpatch` itself, lazily, so
 * the library stays out of the editor's main chunk.
 *
 * Delta format (jsondiffpatch ≥ 0.5): `[new]` added, `[old, new]` changed,
 * `[old, 0, 0]` removed, `["", to, 3]` moved; objects nest; arrays carry
 * `_t: "a"` with `"i"` (new index) and `"_i"` (old index) keys.
 */

export type DiffKind = "added" | "removed" | "changed" | "moved";

export interface DiffRow {
  path: string;
  kind: DiffKind;
  before?: unknown;
  after?: unknown;
}

function join(base: string, key: string, inArray: boolean): string {
  if (inArray) return `${base}[${key}]`;
  return base ? `${base}.${key}` : key;
}

export function diffRows(delta: unknown, base = ""): DiffRow[] {
  if (delta === undefined || delta === null) return [];
  if (Array.isArray(delta)) {
    if (delta.length === 1) return [{ path: base, kind: "added", after: delta[0] }];
    if (delta.length === 2) return [{ path: base, kind: "changed", before: delta[0], after: delta[1] }];
    if (delta.length === 3 && delta[2] === 0) return [{ path: base, kind: "removed", before: delta[0] }];
    if (delta.length === 3 && delta[2] === 3) return [{ path: base, kind: "moved", after: delta[1] }];
    if (delta.length === 3 && delta[2] === 2) return [{ path: base, kind: "changed", after: delta[0] }];
    return [];
  }
  if (typeof delta !== "object") return [];
  const record = delta as Record<string, unknown>;
  const isArray = record._t === "a";
  const rows: DiffRow[] = [];
  const keys = Object.keys(record)
    .filter((key) => key !== "_t")
    .sort((a, b) => {
      const na = Number(a.replace(/^_/, ""));
      const nb = Number(b.replace(/^_/, ""));
      return Number.isNaN(na) || Number.isNaN(nb) ? a.localeCompare(b) : na - nb;
    });
  for (const key of keys) {
    const child = record[key];
    const label = isArray ? key.replace(/^_/, "") : key;
    // An old-index entry that only moved is noise next to its new-index twin.
    if (isArray && key.startsWith("_") && Array.isArray(child) && child[2] === 3) continue;
    rows.push(...diffRows(child, join(base, label, isArray)));
  }
  return rows;
}

/** Compact one-line rendering of a value for a diff row. */
export function preview(value: unknown, max = 140): string {
  if (value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Array items are matched by `id` (nodes, edges, blocks) or `name` (variables) before position. */
export function objectHash(item: object, index?: number): string {
  const record = item as Record<string, unknown>;
  if (typeof record.id === "string") return `id:${record.id}`;
  if (typeof record.name === "string") return `name:${record.name}`;
  return `index:${index ?? JSON.stringify(item)}`;
}
