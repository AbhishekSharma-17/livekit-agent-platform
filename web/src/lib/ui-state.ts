/**
 * Pure `UiState` envelope reducer for the agent → UI protocol
 * (docs/CONTRACTS.md §10).
 *
 * The agent publishes `UiSnapshot` / `UiPatch` JSON on the `lkap.ui.state`
 * text stream and `ActivityEvent` JSON on `lkap.ui.activity`. This module owns
 * every ordering and mutation rule so the React hooks stay thin and the whole
 * protocol is testable without a LiveKit room:
 *
 * - `seq` starts at 1 with a snapshot; every patch increments it.
 * - A patch applies only when `seq === lastSeq + 1`; a gap raises
 *   `needsSnapshot` (the hook then asks the agent via the `get_snapshot` RPC)
 *   and every further patch is discarded until a snapshot arrives.
 * - Snapshots always win when they are newer than what we hold, and they clear
 *   `needsSnapshot`.
 *
 * Types come from `@/contracts/lkap-contracts` (generated; never hand-written).
 */
import type {
  ActivityEvent,
  UiPatch,
  UiPatchOp,
  UiSnapshot,
  UiState,
} from "@/contracts/lkap-contracts";

/** Discriminated union carried on the `lkap.ui.state` topic. */
export type UiStateMessage = UiSnapshot | UiPatch;

/** `UiSnapshot` and `UiPatch` both have an optional `type`, so narrow on shape. */
export function isUiSnapshot(message: UiStateMessage): message is UiSnapshot {
  return "state" in message;
}

/** Newest-first cap the agent applies to `UiState.activity` (CONTRACTS §10). */
export const ACTIVITY_LIMIT = 30;

export interface UiStateStore {
  /** The envelope as currently known. Always fully populated (no `undefined`). */
  state: Required<
    Pick<
      UiState,
      "notes" | "checklist" | "assets" | "activity" | "blocks" | "custom" | "v"
    >
  > &
    UiState;
  /** Last applied `seq`; `0` means "nothing applied yet". */
  seq: number;
  /** `session_id` of the stream we locked onto, or `null` before the first message. */
  sessionId: string | null;
  /** A gap was seen: patches are dropped until a fresh snapshot arrives. */
  needsSnapshot: boolean;
  /** Messages dropped because they were stale or arrived during a gap. */
  droppedCount: number;
}

export type UiStateAction =
  | { type: "message"; message: UiStateMessage }
  | { type: "activity"; event: ActivityEvent }
  | { type: "reset" };

export function emptyUiState(): UiStateStore["state"] {
  return {
    v: 2,
    status: null,
    progress: null,
    notes: [],
    checklist: [],
    assets: [],
    activity: [],
    blocks: {},
    custom: {},
  };
}

export function initialUiStateStore(): UiStateStore {
  return {
    state: emptyUiState(),
    seq: 0,
    sessionId: null,
    needsSnapshot: false,
    droppedCount: 0,
  };
}

/**
 * Fill in the optional slots of a wire `UiState` so consumers never have to
 * null-check. Unknown extra keys are preserved; `v` passes through (the v2
 * worker sends `2`, v1 producers `1`) and defaults to `2`.
 *
 * `blocks` (v2, asks #68) is `{}` when missing — the seq-1 snapshot always
 * carries it, but a v1 producer or a panel test may not.
 */
export function normalizeUiState(state: UiState): UiStateStore["state"] {
  return {
    ...state,
    v: state.v ?? 2,
    status: state.status ?? null,
    progress: state.progress ?? null,
    notes: state.notes ?? [],
    checklist: state.checklist ?? [],
    assets: state.assets ?? [],
    activity: capActivity(state.activity ?? []),
    blocks: isRecord(state.blocks) ? state.blocks : {},
    custom: state.custom ?? {},
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Parse one text-stream payload. Returns `null` for anything that is not a
 * well-formed `UiSnapshot`/`UiPatch` so the caller can retry a stream that is
 * still being chunked in rather than treating it as applied.
 */
export function parseUiStateMessage(raw: string): UiStateMessage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (typeof parsed.seq !== "number" || typeof parsed.session_id !== "string") {
    return null;
  }
  if (parsed.type === "snapshot" && isRecord(parsed.state)) {
    return parsed as unknown as UiSnapshot;
  }
  if (parsed.type === "patch" && Array.isArray(parsed.ops)) {
    return parsed as unknown as UiPatch;
  }
  return null;
}

/** Parse one `lkap.ui.activity` payload. */
export function parseActivityEvent(raw: string): ActivityEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (typeof parsed.id !== "string" || typeof parsed.phase !== "string") {
    return null;
  }
  return parsed as unknown as ActivityEvent;
}

/* -------------------------------------------------------------------------- */
/* JSON-pointer helpers                                                       */
/* -------------------------------------------------------------------------- */

/** Split a JSON-pointer-style path (`/custom/fields/policy`) into segments. */
export function splitPath(path: string): string[] {
  if (path === "") return [];
  const trimmed = path.startsWith("/") ? path.slice(1) : path;
  return trimmed
    .split("/")
    .map((segment) => segment.replace(/~1/g, "/").replace(/~0/g, "~"));
}


type Mutator = (current: unknown) => { value: unknown; drop?: boolean };

/**
 * Immutably apply `mutate` at `segments`, cloning only the containers along the
 * path. Missing intermediate containers are created as plain objects.
 */
function writeAt(target: unknown, segments: string[], mutate: Mutator): unknown {
  if (segments.length === 0) {
    const { value } = mutate(target);
    return value;
  }
  const [head, ...rest] = segments;

  if (Array.isArray(target)) {
    const index = Number(head);
    if (!Number.isInteger(index) || index < 0) return target;
    const next = [...target];
    if (rest.length === 0) {
      const { value, drop } = mutate(next[index]);
      if (drop) {
        next.splice(index, 1);
      } else {
        next[index] = value;
      }
    } else {
      next[index] = writeAt(next[index], rest, mutate);
    }
    return next;
  }

  const base: Record<string, unknown> = isRecord(target) ? { ...target } : {};
  if (rest.length === 0) {
    const { value, drop } = mutate(base[head]);
    if (drop) {
      delete base[head];
    } else {
      base[head] = value;
    }
  } else {
    base[head] = writeAt(base[head], rest, mutate);
  }
  return base;
}

function itemKey(item: unknown): string | null {
  if (!isRecord(item)) return null;
  if (typeof item.key === "string") return item.key;
  if (typeof item.id === "string") return item.id;
  return null;
}

/**
 * List-item identity for `upsert`/`remove`: either the item's `key` or its
 * `id` may carry the match, so both are compared (CONTRACTS §10 — "`upsert`
 * matches list items by `key` or `id`").
 */
function itemMatches(item: unknown, key: string): boolean {
  if (!isRecord(item)) return false;
  return item.key === key || item.id === key;
}

function matchKeyFor(op: UiPatchOp, value: unknown): string | null {
  if (typeof op.key === "string" && op.key !== "") return op.key;
  return itemKey(value);
}

function asList(current: unknown): unknown[] {
  return Array.isArray(current) ? current : [];
}

/** Apply one patch op to a state tree, returning a new tree. */
export function applyOp(state: unknown, op: UiPatchOp): unknown {
  const blockSegments = blockPathSegments(op.path);
  if (blockSegments !== null) {
    const root = isRecord(state) ? state : {};
    return { ...root, blocks: applyBlocksOp(root.blocks, blockSegments, op) };
  }
  const segments = splitPath(op.path);
  const value = op.value;

  switch (op.op) {
    case "set":
      return writeAt(state, segments, () => ({ value }));

    case "append":
      return writeAt(state, segments, (current) => ({
        value: [...asList(current), value],
      }));

    case "upsert": {
      const key = matchKeyFor(op, value);
      return writeAt(state, segments, (current) => {
        const list = asList(current);
        if (key === null) return { value: [...list, value] };
        const index = list.findIndex((item) => itemMatches(item, key));
        if (index === -1) return { value: [...list, value] };
        const next = [...list];
        next[index] = value;
        return { value: next };
      });
    }

    case "remove": {
      const key = typeof op.key === "string" && op.key !== "" ? op.key : null;
      if (key !== null) {
        return writeAt(state, segments, (current) => ({
          value: asList(current).filter((item) => !itemMatches(item, key)),
        }));
      }
      return writeAt(state, segments, () => ({ value: undefined, drop: true }));
    }

    default:
      return state;
  }
}

/* -------------------------------------------------------------------------- */
/* `/blocks/...` — mirrors the agent's `_apply_tree_op`                        */
/* -------------------------------------------------------------------------- */

/**
 * The segments after `/blocks` when `path` targets the v2 block tree, else
 * `null`. Empty segments are dropped and nothing is unescaped, exactly like the agent's `_segments`
 * (`agent/src/lkap_agent/ui/channel.py`), so `/blocks//t/rows` and
 * `/blocks/t/rows` address the same list on both sides.
 */
export function blockPathSegments(path: string): string[] | null {
  // No `~1`/`~0` unescaping: the agent's `_segments` does none, and block ids
  // never contain `/`.
  const segments = path.split("/").filter((segment) => segment !== "");
  return segments[0] === "blocks" ? segments.slice(1) : null;
}

/** Python's `str.isdigit()` for the list-index segments the agent accepts. */
function isIndex(segment: string): boolean {
  return /^\d+$/.test(segment);
}

/** `_tree_item_matches`: a dict whose `key` or `id` equals `key`. */
function blockItemMatches(item: unknown, key: unknown): boolean {
  return isRecord(item) && (item.key === key || item.id === key);
}

/** `_apply_tree_list_op` on a copy of `items`. */
function blockListOp(items: unknown[], op: UiPatchOp): unknown[] {
  const value = op.value;
  switch (op.op) {
    case "append":
      return [...items, value];
    case "upsert": {
      // `candidate = key`, else the value's truthy `key`, else its `id`.
      let candidate: unknown = op.key ?? null;
      if (candidate === null && isRecord(value)) {
        candidate = value.key || value.id || null;
      }
      if (candidate !== null && candidate !== undefined) {
        const index = items.findIndex((item) => blockItemMatches(item, candidate));
        if (index !== -1) {
          const next = [...items];
          next[index] = value;
          return next;
        }
      }
      return [...items, value];
    }
    case "remove":
      return typeof op.key === "string"
        ? items.filter((item) => !blockItemMatches(item, op.key as string))
        : items;
    default:
      return items;
  }
}

/**
 * Immutable twin of the agent's `_apply_tree_op` + `_tree_child`
 * (`agent/src/lkap_agent/ui/channel.py`), so a block patch lands identically
 * in the worker's `UiState` and in the browser:
 *
 * - intermediate dict segments that are missing (or hold a scalar) become
 *   `{}`; a numeric segment indexes a list, and an out-of-range or
 *   non-numeric index into a list makes the whole op a **no-op**;
 * - a list leaf: `set` replaces the item (out of range → no-op), an unkeyed
 *   `remove` deletes it, `append`/`upsert`/keyed `remove` act on the list
 *   stored at that index;
 * - a dict leaf: `set` writes, an unkeyed `remove` deletes the key,
 *   `append`/`upsert`/keyed `remove` act on the list stored there (a
 *   non-list is treated as `[]`);
 * - `upsert`/keyed `remove` match items by `key` or `id`.
 * - `set /blocks` replaces the whole tree; any other op on the root is
 *   ignored (the agent raises).
 */
export function applyBlocksOp(blocks: unknown, segments: string[], op: UiPatchOp): Record<string, unknown> {
  const root: Record<string, unknown> = isRecord(blocks) ? blocks : {};
  if (segments.length === 0) {
    if (op.op !== "set") return root;
    return isRecord(op.value) ? { ...op.value } : {};
  }
  const next = writeBlockTree(root, segments, op);
  return next === NO_OP ? root : (next as Record<string, unknown>);
}

const NO_OP: unique symbol = Symbol("no-op");

function writeBlockTree(container: unknown, segments: string[], op: UiPatchOp): unknown {
  const [head, ...rest] = segments;

  if (Array.isArray(container)) {
    if (!isIndex(head) || Number(head) >= container.length) return NO_OP;
    const index = Number(head);
    const next = [...container];
    if (rest.length === 0) {
      if (op.op === "remove" && typeof op.key !== "string") {
        next.splice(index, 1);
      } else if (op.op === "set") {
        next[index] = op.value;
      } else {
        next[index] = blockListOp(asList(next[index]), op);
      }
      return next;
    }
    const child = next[index];
    const written = writeBlockTree(isRecord(child) || Array.isArray(child) ? child : {}, rest, op);
    if (written === NO_OP) return NO_OP;
    next[index] = written;
    return next;
  }

  const base: Record<string, unknown> = isRecord(container) ? { ...container } : {};
  if (rest.length === 0) {
    if (op.op === "set") {
      base[head] = op.value;
    } else if (op.op === "remove" && typeof op.key !== "string") {
      delete base[head];
    } else {
      base[head] = blockListOp(asList(base[head]), op);
    }
    return base;
  }
  const child = base[head];
  const written = writeBlockTree(isRecord(child) || Array.isArray(child) ? child : {}, rest, op);
  if (written === NO_OP) return NO_OP;
  base[head] = written;
  return base;
}

export function applyOps(
  state: UiStateStore["state"],
  ops: UiPatchOp[],
): UiStateStore["state"] {
  let next: unknown = state;
  for (const op of ops) {
    next = applyOp(next, op);
  }
  return normalizeUiState(next as UiState);
}

/* -------------------------------------------------------------------------- */
/* Activity                                                                   */
/* -------------------------------------------------------------------------- */

function capActivity(events: ActivityEvent[]): ActivityEvent[] {
  const sorted = [...events].sort((a, b) => a.ts - b.ts);
  return sorted.length > ACTIVITY_LIMIT
    ? sorted.slice(sorted.length - ACTIVITY_LIMIT)
    : sorted;
}

/** Merge one activity event into the ring buffer (same `id` replaces). */
export function mergeActivity(
  events: ActivityEvent[],
  event: ActivityEvent,
): ActivityEvent[] {
  const index = events.findIndex((existing) => existing.id === event.id);
  const next = index === -1 ? [...events, event] : [...events];
  if (index !== -1) next[index] = event;
  return capActivity(next);
}

/* -------------------------------------------------------------------------- */
/* Reducer                                                                    */
/* -------------------------------------------------------------------------- */

export function uiStateReducer(
  store: UiStateStore,
  action: UiStateAction,
): UiStateStore {
  switch (action.type) {
    case "reset":
      return initialUiStateStore();

    case "activity": {
      const activity = mergeActivity(store.state.activity, action.event);
      if (activity === store.state.activity) return store;
      return { ...store, state: { ...store.state, activity } };
    }

    case "message": {
      const message = action.message;

      // A different session on the same room means a new agent job took over;
      // treat its snapshot as authoritative and ignore its patches until then.
      const sameSession =
        store.sessionId === null || store.sessionId === message.session_id;

      if (isUiSnapshot(message)) {
        if (sameSession && message.seq <= store.seq && !store.needsSnapshot) {
          return { ...store, droppedCount: store.droppedCount + 1 };
        }
        return {
          state: normalizeUiState(message.state),
          seq: message.seq,
          sessionId: message.session_id,
          needsSnapshot: false,
          droppedCount: store.droppedCount,
        };
      }

      if (!sameSession) {
        return {
          ...store,
          needsSnapshot: true,
          droppedCount: store.droppedCount + 1,
        };
      }
      if (store.needsSnapshot || message.seq <= store.seq) {
        return { ...store, droppedCount: store.droppedCount + 1 };
      }
      if (message.seq !== store.seq + 1) {
        return {
          ...store,
          needsSnapshot: true,
          droppedCount: store.droppedCount + 1,
        };
      }
      return {
        ...store,
        state: applyOps(store.state, message.ops),
        seq: message.seq,
        sessionId: message.session_id,
      };
    }

    default:
      return store;
  }
}
