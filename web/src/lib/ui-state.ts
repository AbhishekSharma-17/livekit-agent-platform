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
      "notes" | "checklist" | "assets" | "activity" | "custom" | "v"
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
    v: 1,
    status: null,
    progress: null,
    notes: [],
    checklist: [],
    assets: [],
    activity: [],
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
 * null-check. Unknown extra keys are preserved.
 */
export function normalizeUiState(state: UiState): UiStateStore["state"] {
  return {
    ...state,
    v: 1,
    status: state.status ?? null,
    progress: state.progress ?? null,
    notes: state.notes ?? [],
    checklist: state.checklist ?? [],
    assets: state.assets ?? [],
    activity: capActivity(state.activity ?? []),
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
