"use client";

/**
 * `useCaptionsStream` — the `lkap.captions` reader (V5-31 -> V5-35,
 * `lkap_contracts.ui_protocol.TOPIC_UI_CAPTIONS`): one `CaptionSegment` JSON
 * per text-stream message, both sides of the conversation. An interim
 * message (`final: false`) is replaced in place by a later one with the same
 * `id` — the final one closes the utterance — so a caller reads "the current
 * state of every utterance seen so far", not an event log.
 *
 * `@livekit/components-react@2.9.24`'s own `useTextStream` returns the raw
 * `TextStreamData[]` (every message ever received, unparsed, never merged by
 * an application-level `id`) — the wrong shape for "replace this utterance",
 * so this reads `room.registerTextStreamHandler` directly, the same way
 * `hooks/useByteStream.ts` reads `registerByteStreamHandler` for
 * `lkap.ui.asset` instead of using a components-react hook.
 *
 * **Only one handler may be registered per topic per room** (`useByteStream`'s
 * own note, and `livekit-client`'s `registerTextStreamHandler` throws on a
 * second registration for the same topic). A panel may legally hold more
 * than one `captions` block (V5-15/V5-19's convention allows several blocks
 * of one type), so registration is centralized per room here — the first
 * mounted `useCaptionsStream` registers the handler and later ones just add
 * a listener — rather than each block registering its own.
 */
import { useEffect, useState } from "react";
import { useMaybeRoomContext } from "@livekit/components-react";
import type { Room, TextStreamReader } from "livekit-client";

import type { CaptionSegment } from "@/contracts/lkap-contracts";

/** `lkap_contracts.ui_protocol.TOPIC_UI_CAPTIONS`. Not exported to TS (a plain Python constant, like `TOPIC_UI_UPLOAD` in `./upload.ts`). */
export const TOPIC_UI_CAPTIONS = "lkap.captions";

/** Segments kept per room before the oldest (by first-seen order) are dropped. */
const MAX_SEGMENTS = 100;

type Listener = (segments: CaptionSegment[]) => void;

interface RoomSubscription {
  order: string[];
  byId: Map<string, CaptionSegment>;
  listeners: Set<Listener>;
}

const subscriptions = new WeakMap<Room, RoomSubscription>();

function isCaptionSegment(value: unknown): value is CaptionSegment {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { id?: unknown }).id === "string" &&
    typeof (value as { text?: unknown }).text === "string" &&
    typeof (value as { final?: unknown }).final === "boolean" &&
    ((value as { speaker?: unknown }).speaker === "user" || (value as { speaker?: unknown }).speaker === "agent")
  );
}

function notify(sub: RoomSubscription): void {
  const ordered = sub.order.map((id) => sub.byId.get(id)).filter((segment): segment is CaptionSegment => segment !== undefined);
  for (const listener of sub.listeners) listener(ordered);
}

function applySegment(sub: RoomSubscription, segment: CaptionSegment): void {
  if (!sub.byId.has(segment.id)) sub.order.push(segment.id);
  sub.byId.set(segment.id, segment);
  while (sub.order.length > MAX_SEGMENTS) {
    const oldest = sub.order.shift();
    if (oldest !== undefined) sub.byId.delete(oldest);
  }
  notify(sub);
}

function subscribe(room: Room, listener: Listener): () => void {
  let sub = subscriptions.get(room);
  if (!sub) {
    sub = { order: [], byId: new Map(), listeners: new Set() };
    subscriptions.set(room, sub);
    room.registerTextStreamHandler(TOPIC_UI_CAPTIONS, (reader: TextStreamReader) => {
      void reader.readAll().then((text) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(text);
        } catch {
          return;
        }
        if (isCaptionSegment(parsed)) applySegment(sub!, parsed);
      });
    });
  }
  sub.listeners.add(listener);
  listener(sub.order.map((id) => sub!.byId.get(id)).filter((s): s is CaptionSegment => s !== undefined));
  return () => {
    sub!.listeners.delete(listener);
    if (sub!.listeners.size === 0) {
      room.unregisterTextStreamHandler(TOPIC_UI_CAPTIONS);
      subscriptions.delete(room);
    }
  };
}

export interface UseCaptionsStreamOptions {
  /** Room to bind to; defaults to the surrounding `RoomContext`. */
  room?: Room;
}

/**
 * Every caption segment seen this session, in first-seen order, an interim
 * message replaced in place by its final one. Outside a room (the console
 * preview, a test with no `RoomContext`, a phone call or text session with
 * no captions block) this returns `[]` and never throws.
 */
export function useCaptionsStream(options: UseCaptionsStreamOptions = {}): CaptionSegment[] {
  const contextRoom = useMaybeRoomContext();
  const room = options.room ?? contextRoom;
  const [segments, setSegments] = useState<CaptionSegment[]>([]);

  useEffect(() => {
    if (!room) {
      setSegments([]);
      return undefined;
    }
    return subscribe(room, setSegments);
  }, [room]);

  return segments;
}

/** The most recent segment for `speaker` (the "current utterance" a captions block shows), or `null`. */
export function latestSegmentFor(segments: CaptionSegment[], speaker: CaptionSegment["speaker"]): CaptionSegment | null {
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    if (segments[i]?.speaker === speaker) return segments[i]!;
  }
  return null;
}
