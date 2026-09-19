import { describe, expect, it } from "vitest";

import type {
  ActivityEvent,
  Note,
  UiPatch,
  UiSnapshot,
  UiState,
} from "@/contracts/lkap-contracts";
import {
  ACTIVITY_LIMIT,
  applyOp,
  initialUiStateStore,
  mergeActivity,
  normalizeUiState,
  parseActivityEvent,
  parseUiStateMessage,
  splitPath,
  uiStateReducer,
  type UiStateMessage,
  type UiStateStore,
} from "@/lib/ui-state";

const SESSION = "sess-1";

function note(id: string, text: string, key?: string): Note {
  return { id, text, kind: "note", tone: "neutral", ts: 1, key: key ?? null };
}

function snapshot(seq: number, state: UiState): UiSnapshot {
  return { v: 1, type: "snapshot", seq, session_id: SESSION, state };
}

function patch(seq: number, ops: UiPatch["ops"]): UiPatch {
  return { v: 1, type: "patch", seq, session_id: SESSION, ops };
}

function activity(id: string, ts: number, phase: ActivityEvent["phase"]) {
  return {
    v: 1,
    id,
    ts,
    source: "lookup_policy",
    label: "Policy desk",
    phase,
    headline: `${id} ${phase}`,
    urgent: false,
  } satisfies ActivityEvent;
}

function apply(store: UiStateStore, ...messages: UiStateMessage[]) {
  return messages.reduce(
    (acc, message) => uiStateReducer(acc, { type: "message", message }),
    store,
  );
}

describe("parseUiStateMessage", () => {
  it("parses a snapshot envelope", () => {
    const parsed = parseUiStateMessage(
      JSON.stringify(snapshot(1, { v: 1, notes: [] })),
    );
    expect(parsed?.type).toBe("snapshot");
  });

  it("parses a patch envelope", () => {
    const parsed = parseUiStateMessage(
      JSON.stringify(patch(2, [{ op: "set", path: "/progress", value: 40 }])),
    );
    expect(parsed?.type).toBe("patch");
  });

  it("returns null for a partially streamed chunk so it can be retried", () => {
    expect(parseUiStateMessage('{"type":"snap')).toBeNull();
  });

  it("returns null for JSON that is not a state message", () => {
    expect(parseUiStateMessage('{"hello":"world"}')).toBeNull();
    expect(parseUiStateMessage(JSON.stringify({ type: "patch", seq: 1 }))).toBe(
      null,
    );
  });
});

describe("parseActivityEvent", () => {
  it("parses an activity event", () => {
    expect(parseActivityEvent(JSON.stringify(activity("a", 1, "running")))?.id)
      .toBe("a");
  });

  it("rejects malformed payloads", () => {
    expect(parseActivityEvent("not json")).toBeNull();
    expect(parseActivityEvent('{"id":"a"}')).toBeNull();
  });
});

describe("splitPath", () => {
  it("splits and unescapes JSON-pointer segments", () => {
    expect(splitPath("/custom/fields/policy")).toEqual([
      "custom",
      "fields",
      "policy",
    ]);
    expect(splitPath("/custom/a~1b")).toEqual(["custom", "a/b"]);
    expect(splitPath("/")).toEqual([""]);
  });
});

describe("normalizeUiState", () => {
  it("fills every optional slot", () => {
    const state = normalizeUiState({ v: 1 });
    expect(state.notes).toEqual([]);
    expect(state.checklist).toEqual([]);
    expect(state.assets).toEqual([]);
    expect(state.activity).toEqual([]);
    expect(state.custom).toEqual({});
    expect(state.status).toBeNull();
    expect(state.progress).toBeNull();
  });
});

describe("applyOp", () => {
  const base = normalizeUiState({ v: 1, notes: [note("n1", "first", "k1")] });

  it("set writes a scalar at a nested path, creating containers", () => {
    const next = applyOp(base, {
      op: "set",
      path: "/custom/fields/policy",
      value: "H0-44721",
    }) as UiState;
    expect(next.custom?.fields).toEqual({ policy: "H0-44721" });
  });

  it("set does not mutate the input", () => {
    applyOp(base, { op: "set", path: "/progress", value: 50 });
    expect(base.progress).toBeNull();
  });

  it("append pushes onto a list and creates it when missing", () => {
    const next = applyOp(base, {
      op: "append",
      path: "/notes",
      value: note("n2", "second"),
    }) as UiState;
    expect(next.notes?.map((item) => item.id)).toEqual(["n1", "n2"]);

    const created = applyOp(base, {
      op: "append",
      path: "/custom/log",
      value: "line",
    }) as UiState;
    expect(created.custom?.log).toEqual(["line"]);
  });

  it("upsert replaces by op.key", () => {
    const next = applyOp(base, {
      op: "upsert",
      path: "/notes",
      key: "k1",
      value: note("n1b", "updated", "k1"),
    }) as UiState;
    expect(next.notes).toHaveLength(1);
    expect(next.notes?.[0]?.text).toBe("updated");
  });

  it("upsert falls back to the value's own key then id", () => {
    const byValueKey = applyOp(base, {
      op: "upsert",
      path: "/notes",
      value: note("nX", "updated", "k1"),
    }) as UiState;
    expect(byValueKey.notes).toHaveLength(1);

    const byId = applyOp(base, {
      op: "upsert",
      path: "/notes",
      value: note("n1", "by id"),
    }) as UiState;
    expect(byId.notes).toHaveLength(1);
    expect(byId.notes?.[0]?.text).toBe("by id");
  });

  it("upsert appends when nothing matches", () => {
    const next = applyOp(base, {
      op: "upsert",
      path: "/notes",
      key: "other",
      value: note("n2", "second", "other"),
    }) as UiState;
    expect(next.notes).toHaveLength(2);
  });

  it("remove with a key drops the matching list item", () => {
    const next = applyOp(base, {
      op: "remove",
      path: "/notes",
      key: "k1",
    }) as UiState;
    expect(next.notes).toEqual([]);
  });

  it("remove without a key deletes the property at the path", () => {
    const withCustom = applyOp(base, {
      op: "set",
      path: "/custom/draft",
      value: "x",
    });
    const next = applyOp(withCustom, {
      op: "remove",
      path: "/custom/draft",
    }) as UiState;
    expect(next.custom && "draft" in next.custom).toBe(false);
  });

  it("ignores an unknown op", () => {
    const next = applyOp(base, {
      op: "explode" as never,
      path: "/notes",
    });
    expect(next).toBe(base);
  });
});

describe("uiStateReducer — ordering", () => {
  it("applies an ordered snapshot then patches", () => {
    const store = apply(
      initialUiStateStore(),
      snapshot(1, { v: 1, notes: [note("n1", "first", "k1")] }),
      patch(2, [{ op: "set", path: "/progress", value: 25 }]),
      patch(3, [
        { op: "append", path: "/notes", value: note("n2", "second") },
      ]),
    );
    expect(store.seq).toBe(3);
    expect(store.sessionId).toBe(SESSION);
    expect(store.needsSnapshot).toBe(false);
    expect(store.state.progress).toBe(25);
    expect(store.state.notes.map((item) => item.id)).toEqual(["n1", "n2"]);
  });

  it("raises needsSnapshot on a gap and discards later patches", () => {
    let store = apply(initialUiStateStore(), snapshot(1, { v: 1 }));
    store = apply(store, patch(4, [
      { op: "set", path: "/progress", value: 99 },
    ]));
    expect(store.needsSnapshot).toBe(true);
    expect(store.state.progress).toBeNull();
    expect(store.seq).toBe(1);

    store = apply(store, patch(5, [
      { op: "set", path: "/progress", value: 100 },
    ]));
    expect(store.state.progress).toBeNull();
    expect(store.droppedCount).toBe(2);
  });

  it("recovers from a gap when a fresh snapshot arrives", () => {
    let store = apply(
      initialUiStateStore(),
      snapshot(1, { v: 1 }),
      patch(4, [{ op: "set", path: "/progress", value: 99 }]),
    );
    expect(store.needsSnapshot).toBe(true);

    store = apply(store, snapshot(5, { v: 1, progress: 60 }));
    expect(store.needsSnapshot).toBe(false);
    expect(store.seq).toBe(5);
    expect(store.state.progress).toBe(60);

    store = apply(store, patch(6, [{ op: "set", path: "/progress", value: 70 }]));
    expect(store.state.progress).toBe(70);
  });

  it("drops replayed patches and stale snapshots", () => {
    const store = apply(
      initialUiStateStore(),
      snapshot(3, { v: 1, progress: 10 }),
      patch(2, [{ op: "set", path: "/progress", value: 1 }]),
      snapshot(2, { v: 1, progress: 2 }),
    );
    expect(store.seq).toBe(3);
    expect(store.state.progress).toBe(10);
    expect(store.droppedCount).toBe(2);
  });

  it("takes over when a new session_id sends a snapshot", () => {
    let store = apply(initialUiStateStore(), snapshot(7, { v: 1, progress: 10 }));
    store = uiStateReducer(store, {
      type: "message",
      message: {
        v: 1,
        type: "patch",
        seq: 8,
        session_id: "sess-2",
        ops: [{ op: "set", path: "/progress", value: 11 }],
      },
    });
    expect(store.needsSnapshot).toBe(true);
    expect(store.state.progress).toBe(10);

    store = uiStateReducer(store, {
      type: "message",
      message: {
        v: 1,
        type: "snapshot",
        seq: 1,
        session_id: "sess-2",
        state: { v: 1, progress: 0 },
      },
    });
    expect(store.sessionId).toBe("sess-2");
    expect(store.seq).toBe(1);
    expect(store.state.progress).toBe(0);
  });

  it("resets to an empty store", () => {
    const store = apply(initialUiStateStore(), snapshot(1, { v: 1, progress: 5 }));
    expect(uiStateReducer(store, { type: "reset" })).toEqual(
      initialUiStateStore(),
    );
  });
});

describe("uiStateReducer — activity", () => {
  it("appends events and replaces by id", () => {
    let store = uiStateReducer(initialUiStateStore(), {
      type: "activity",
      event: activity("call-1", 1, "running"),
    });
    store = uiStateReducer(store, {
      type: "activity",
      event: activity("call-1", 2, "done"),
    });
    expect(store.state.activity).toHaveLength(1);
    expect(store.state.activity[0]?.phase).toBe("done");
  });

  it("keeps only the newest events, sorted by ts", () => {
    let events: ActivityEvent[] = [];
    for (let i = 0; i < ACTIVITY_LIMIT + 5; i += 1) {
      events = mergeActivity(events, activity(`call-${i}`, i, "done"));
    }
    expect(events).toHaveLength(ACTIVITY_LIMIT);
    expect(events[0]?.id).toBe("call-5");
    expect(events.at(-1)?.id).toBe(`call-${ACTIVITY_LIMIT + 4}`);
  });

  it("survives a snapshot carrying its own activity list", () => {
    const store = apply(
      initialUiStateStore(),
      snapshot(1, { v: 1, activity: [activity("a", 5, "done"), activity("b", 1, "done")] }),
    );
    expect(store.state.activity.map((event) => event.id)).toEqual(["b", "a"]);
  });
});
