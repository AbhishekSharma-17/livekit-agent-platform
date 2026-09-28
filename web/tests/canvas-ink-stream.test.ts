import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  InkSender,
  MAX_INK_MESSAGE_BYTES,
  MAX_INK_MESSAGES_PER_S,
  randomStrokeId,
  type InkRoom,
} from "@/panels/composite/ink-stream";
import { TOPIC_UI_INK } from "@/lib/livekit";

/** Mirrors `lkap_contracts.ui_protocol.CANVAS_MARK_ID_PATTERN`. */
const CANVAS_MARK_ID_PATTERN_RE = /^[A-Za-z0-9_.:-]{1,64}$/;

/**
 * V6-14, ask #92: `InkSender` batches a board's strokes onto `lkap.ui.ink`, mirroring the
 * worker's own limits (`agent/src/lkap_agent/ui/ink.py`) so the page backs off instead of
 * getting messages silently dropped: at most `MAX_INK_MESSAGE_BYTES` of JSON a message, at
 * most `MAX_INK_MESSAGES_PER_S` messages a second, `compress: false` (until a live check
 * shows the worker decodes a compressed text stream).
 */

function fakeRoom() {
  const sendText = vi.fn(async () => ({}));
  const room: InkRoom = { localParticipant: { sendText } };
  return { room, sendText };
}

function parseCalls(sendText: ReturnType<typeof vi.fn>): Record<string, unknown>[] {
  return sendText.mock.calls.map(([json]) => JSON.parse(json as string) as Record<string, unknown>);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("randomStrokeId", () => {
  it("matches the wire's stroke id pattern (letters, digits, _.:-, 1-64 chars)", () => {
    for (let i = 0; i < 20; i += 1) {
      const id = randomStrokeId();
      expect(id).toMatch(CANVAS_MARK_ID_PATTERN_RE);
      expect(id.length).toBeLessThanOrEqual(64);
    }
  });

  it("never repeats across many calls in the same tick", () => {
    const ids = new Set(Array.from({ length: 500 }, () => randomStrokeId()));
    expect(ids.size).toBe(500);
  });
});

describe("InkSender — sending on lkap.ui.ink", () => {
  it("sends compress:false on the ink topic (ask #92)", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    sender.beginFreehandStroke("pen", "#1f2937", 3, [0.1, 0.2]);
    await vi.advanceTimersByTimeAsync(100);
    expect(sendText).toHaveBeenCalledWith(expect.any(String), { topic: TOPIC_UI_INK, compress: false });
  });

  it("the first message of a stroke carries tool, colour and width; later ones do not", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    const strokeId = sender.beginFreehandStroke("pen", "#1f2937", 3, [0.1, 0.2]);
    sender.addPoints(strokeId, [[0.11, 0.21]]);
    sender.endStroke();
    await vi.advanceTimersByTimeAsync(0);
    const calls = parseCalls(sendText);
    expect(calls).toHaveLength(1); // coalesced into one message (same stroke, still under budget)
    expect(calls[0]).toMatchObject({ v: 1, block_id: "board", stroke_id: strokeId, op: "add", tool: "pen", color: "#1f2937", width: 3 });
    expect(calls[0].points).toEqual([
      [0.1, 0.2],
      [0.11, 0.21],
    ]);
  });

  it("coalesces points added within the same flush window into one message", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    const strokeId = sender.beginFreehandStroke("pen", "#1f2937", 3, [0, 0]);
    for (let i = 1; i <= 10; i += 1) sender.addPoints(strokeId, [[i / 100, i / 100]]);
    await vi.advanceTimersByTimeAsync(100);
    expect(sendText).toHaveBeenCalledTimes(1);
    const [message] = parseCalls(sendText);
    expect((message.points as unknown[]).length).toBe(11); // the begin point + 10 more
  });

  it("splits a long stroke into several messages once the byte budget is exceeded", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    const strokeId = sender.beginFreehandStroke("pen", "#1f2937", 3, [0, 0]);
    // Points with pressure run ~23 JSON characters each — well over 128 of them is needed
    // to cross the 2 KiB cap (the point count alone never binds; the byte cap does).
    const points: [number, number, number][] = Array.from({ length: 400 }, (_, i) => [
      (i % 100) / 100,
      (i % 100) / 100,
      0.75,
    ]);
    sender.addPoints(strokeId, points);
    sender.endStroke();
    await vi.advanceTimersByTimeAsync(0);
    // Draining more than 20 messages needs the token bucket to refill past the first second.
    await vi.advanceTimersByTimeAsync(2000);
    const calls = parseCalls(sendText);
    expect(calls.length).toBeGreaterThan(1);
    for (const call of calls) {
      expect(new TextEncoder().encode(JSON.stringify(call)).length).toBeLessThanOrEqual(MAX_INK_MESSAGE_BYTES);
    }
    const totalPoints = calls.reduce((sum, call) => sum + (call.points as unknown[]).length, 0);
    expect(totalPoints).toBe(points.length + 1); // + the point `beginFreehandStroke` sent
  });

  it("never sends more than MAX_INK_MESSAGES_PER_S messages within one second", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    // 30 distinct strokes (so nothing coalesces) started back to back.
    for (let i = 0; i < 30; i += 1) {
      sender.beginFreehandStroke("pen", "#1f2937", 3, [i / 100, 0]);
    }
    await vi.advanceTimersByTimeAsync(100);
    expect(sendText.mock.calls.length).toBeLessThanOrEqual(MAX_INK_MESSAGES_PER_S);
    await vi.advanceTimersByTimeAsync(1000);
    expect(sendText.mock.calls.length).toBe(30);
  });

  it("sends a box/arrow stroke as one message with exactly its start and end point", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    sender.sendShapeStroke("arrow", "#dc2626", 3, [0.1, 0.1], [0.9, 0.4]);
    await vi.advanceTimersByTimeAsync(100);
    const calls = parseCalls(sendText);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ op: "add", tool: "arrow", points: [[0.1, 0.1], [0.9, 0.4]] });
  });

  it("erase and clear flush immediately, without waiting for the 100ms tick", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    sender.erase("c:some-stroke");
    await vi.advanceTimersByTimeAsync(0);
    expect(sendText).toHaveBeenCalledTimes(1);
    expect(parseCalls(sendText)[0]).toMatchObject({ op: "erase", stroke_id: "c:some-stroke" });

    sendText.mockClear();
    sender.clear();
    await vi.advanceTimersByTimeAsync(0);
    expect(sendText).toHaveBeenCalledTimes(1);
    expect(parseCalls(sendText)[0]).toMatchObject({ op: "clear" });
    expect(parseCalls(sendText)[0].stroke_id).toBeUndefined();
  });

  it("a clear drops anything still queued for the board", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    // Queue up 25 distinct strokes (more than the bucket lets through in one tick), then clear.
    for (let i = 0; i < 25; i += 1) sender.beginFreehandStroke("pen", "#1f2937", 3, [i / 100, 0]);
    sender.clear();
    await vi.advanceTimersByTimeAsync(0);
    const calls = parseCalls(sendText);
    // Only what the bucket already let through before `clear()`, plus the clear itself.
    expect(calls[calls.length - 1]).toMatchObject({ op: "clear" });
    expect(calls.length).toBeLessThan(26);
  });

  it("dispose() stops the flush timer and drops the queue", async () => {
    const { room, sendText } = fakeRoom();
    const sender = new InkSender(room, "board");
    sender.beginFreehandStroke("pen", "#1f2937", 3, [0, 0]);
    sender.dispose();
    await vi.advanceTimersByTimeAsync(1000);
    expect(sendText).not.toHaveBeenCalled();
  });

  it("swallows a sendText rejection (mid-reconnect) instead of throwing", async () => {
    const room: InkRoom = { localParticipant: { sendText: vi.fn(async () => Promise.reject(new Error("not connected"))) } };
    const sender = new InkSender(room, "board");
    expect(() => sender.beginFreehandStroke("pen", "#1f2937", 3, [0, 0])).not.toThrow();
    // Neither the flush nor the surrounding test run throws or rejects unhandled.
    await vi.advanceTimersByTimeAsync(200);
  });
});
