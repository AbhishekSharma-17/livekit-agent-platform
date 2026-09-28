"use client";

/**
 * The caller's side of `lkap.ui.ink` (V6-14, ask #92; CONTRACTS §10 D-V6-16): batches a
 * board's strokes into `InkMessage`s and sends them on
 * `room.localParticipant.sendText(json, {topic: TOPIC_UI_INK, compress: false})` —
 * `compress: false` until a live check shows the worker (livekit 1.1.18's FFI) decodes a
 * compressed text stream (ask #92: sending compressed risks every stroke arriving as
 * `malformed` in the worker log).
 *
 * The worker takes at most `MAX_INK_MESSAGES_PER_S` (20) messages a second from the whole
 * page and at most `MAX_INK_MESSAGE_BYTES` (2 KiB) of JSON each (`agent/src/lkap_agent/
 * ui/ink.py`) — **the byte cap binds well before the 128-point cap does**: at four decimal
 * places one `[x, y, pressure]` point is around 23 JSON characters, so 128 of them alone
 * would run to about 2.9 KB before the envelope even starts. `InkSender` mirrors the
 * worker's own token bucket (so the page backs off instead of getting every message past
 * the limit silently dropped), flushes on a 100 ms tick (matching the worker's own
 * `lkap.ui.state` patch cadence) and on every `endStroke`, and coalesces queued `add`s for
 * the same stroke while the bucket is empty rather than sending them as separate messages.
 *
 * `box`/`arrow` strokes are "first and last point" shapes (CONTRACTS §10): `endBoxOrArrow`
 * sends exactly one `add` with two points, never a stream of intermediate ones — the live
 * SVG board and `rasterise.ts` both draw them the same way, from `points[0]`/`points.at(-1)`.
 */
import { useEffect, useMemo } from "react";

import { TOPIC_UI_INK } from "@/lib/livekit";

import { normalizePoint, type InkPoint } from "../blocks/canvas/geometry";
import type { CanvasToolChoice } from "../blocks/canvas/permissions";

/** The wire tool names `InkMessage.tool` accepts (`text`/`eraser` never travel as a stroke's tool). */
export type WireInkTool = Exclude<CanvasToolChoice, "eraser">;

export interface InkRoom {
  localParticipant: {
    sendText(text: string, options?: { topic?: string; compress?: boolean }): Promise<unknown>;
  };
}

interface WireInkMessage {
  v: 1;
  block_id: string;
  stroke_id?: string;
  op: "add" | "erase" | "clear";
  tool?: WireInkTool;
  points?: InkPoint[];
  color?: string;
  width?: number;
}

/** Mirrors `agent/src/lkap_agent/ui/ink.py`'s limits (server-authoritative; this only avoids drops). */
export const MAX_INK_MESSAGE_BYTES = 2048;
export const MAX_INK_MESSAGES_PER_S = 20;
/** Matches the worker's own patch cadence (`INK_FLUSH_INTERVAL_S`, `ui/channel.py`). */
export const INK_FLUSH_MS = 100;

let strokeCounter = 0;

/** A short, unique stroke id (`CANVAS_MARK_ID_PATTERN`: letters, digits, `_.:-`). */
export function randomStrokeId(): string {
  strokeCounter = (strokeCounter + 1) % 1_000_000;
  return `c:${Date.now().toString(36)}${strokeCounter.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Splits `points` into the fewest chunks whose JSON (wrapped in `base`) stays under the byte cap. */
function chunkToByteBudget(base: Omit<WireInkMessage, "points">, points: readonly InkPoint[]): InkPoint[][] {
  const chunks: InkPoint[][] = [];
  let current: InkPoint[] = [];
  for (const point of points) {
    const candidate = [...current, point];
    if (byteLength(JSON.stringify({ ...base, points: candidate })) > MAX_INK_MESSAGE_BYTES && current.length > 0) {
      chunks.push(current);
      current = [point];
    } else {
      current = candidate;
    }
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}

/**
 * The batching sender for one board. Construct one per `(room, blockId)` pair
 * (`useInkSender`); `dispose()` stops its flush timer.
 */
export class InkSender {
  private tokens = MAX_INK_MESSAGES_PER_S;
  private lastRefill = InkSender.now();
  private queue: WireInkMessage[] = [];
  private flushing = false;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(
    private readonly room: InkRoom,
    private readonly blockId: string,
  ) {}

  private static now(): number {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  private refill(): void {
    const now = InkSender.now();
    const elapsedS = Math.max(0, now - this.lastRefill) / 1000;
    this.tokens = Math.min(MAX_INK_MESSAGES_PER_S, this.tokens + elapsedS * MAX_INK_MESSAGES_PER_S);
    this.lastRefill = now;
  }

  private scheduleFlush(delayMs: number): void {
    if (this.disposed || this.flushTimer !== null) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      void this.flush();
    }, delayMs);
  }

  private enqueue(message: WireInkMessage): void {
    if (this.disposed) return;
    const last = this.queue[this.queue.length - 1];
    if (last && last.op === "add" && message.op === "add" && last.stroke_id === message.stroke_id) {
      const merged: WireInkMessage = { ...last, points: [...(last.points ?? []), ...(message.points ?? [])] };
      if (byteLength(JSON.stringify(merged)) <= MAX_INK_MESSAGE_BYTES) {
        this.queue[this.queue.length - 1] = merged;
        this.scheduleFlush(INK_FLUSH_MS);
        return;
      }
    }
    this.queue.push(message);
    this.scheduleFlush(INK_FLUSH_MS);
  }

  private async flush(): Promise<void> {
    if (this.flushing || this.disposed) return;
    this.flushing = true;
    try {
      while (this.queue.length > 0) {
        this.refill();
        if (this.tokens < 1) {
          this.scheduleFlush(50);
          return;
        }
        this.tokens -= 1;
        const message = this.queue.shift();
        if (!message) continue;
        try {
          // `sendText` throws mid-reconnect; a dropped stroke piece is better than an
          // unhandled rejection breaking the whole board.
          await this.room.localParticipant.sendText(JSON.stringify(message), {
            topic: TOPIC_UI_INK,
            compress: false,
          });
        } catch {
          // Ignored — see above.
        }
      }
    } finally {
      this.flushing = false;
    }
  }

  /** Starts a freehand stroke (`pen`/`highlighter`): the first point fixes tool, colour and width. */
  beginFreehandStroke(tool: WireInkTool, color: string, width: number, point: InkPoint): string {
    const strokeId = randomStrokeId();
    this.enqueue({ v: 1, block_id: this.blockId, stroke_id: strokeId, op: "add", tool, color, width, points: [normalizePoint(point)] });
    return strokeId;
  }

  /** Continues a freehand stroke with more points (batched, split to the byte cap). */
  addPoints(strokeId: string, points: readonly InkPoint[]): void {
    if (points.length === 0) return;
    const normalized = points.map(normalizePoint);
    const base = { v: 1 as const, block_id: this.blockId, stroke_id: strokeId, op: "add" as const };
    for (const chunk of chunkToByteBudget(base, normalized)) {
      this.enqueue({ ...base, points: chunk });
    }
  }

  /** Sends a `box`/`arrow` stroke in one message: exactly its start and end point. */
  sendShapeStroke(tool: WireInkTool, color: string, width: number, from: InkPoint, to: InkPoint): string {
    const strokeId = randomStrokeId();
    this.enqueue({
      v: 1,
      block_id: this.blockId,
      stroke_id: strokeId,
      op: "add",
      tool,
      color,
      width,
      points: [normalizePoint(from), normalizePoint(to)],
    });
    return strokeId;
  }

  /** Flushes right away rather than waiting for the 100 ms tick (called on pointer-up). */
  endStroke(): void {
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    void this.flush();
  }

  /** The eraser, or "undo" (erasing the caller's own most recent stroke). */
  erase(strokeId: string): void {
    this.enqueue({ v: 1, block_id: this.blockId, op: "erase", stroke_id: strokeId });
    this.endStroke();
  }

  /** Every one of the caller's strokes on this board goes. */
  clear(): void {
    // A clear supersedes anything still queued for this board.
    this.queue = [];
    this.enqueue({ v: 1, block_id: this.blockId, op: "clear" });
    this.endStroke();
  }

  dispose(): void {
    this.disposed = true;
    if (this.flushTimer !== null) clearTimeout(this.flushTimer);
    this.queue = [];
  }
}

/** One `InkSender` per `(room, blockId)`, disposed on unmount or when either changes. `undefined` outside a room. */
export function useInkSender(room: InkRoom | undefined, blockId: string): InkSender | undefined {
  const sender = useMemo(() => (room ? new InkSender(room, blockId) : undefined), [room, blockId]);
  useEffect(() => () => sender?.dispose(), [sender]);
  return sender;
}
