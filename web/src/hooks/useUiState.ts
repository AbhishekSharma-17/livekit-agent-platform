"use client";

/**
 * `useUiState` — CONTRACTS §10.
 *
 * Binds the three agent → UI channels to the pure reducer in
 * `@/lib/ui-state`:
 *
 * - `lkap.ui.state`    text stream  → `UiSnapshot` / `UiPatch`
 * - `lkap.ui.activity` text stream  → `ActivityEvent`
 * - `lkap.ui.asset`    byte stream  → `asset_id → objectURL`
 *
 * On a `seq` gap the reducer raises `needsSnapshot` and this hook asks the
 * agent for a fresh snapshot over the `lkap.agent.action` RPC.
 *
 * Mount once per session: LiveKit allows a single handler per topic per room.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useSessionContext, useTextStream } from "@livekit/components-react";

import type { UiSnapshot } from "@/contracts/lkap-contracts";
import { useAgentRpc } from "@/hooks/useAgentRpc";
import { useByteStream, type ByteStreamAssetInfo } from "@/hooks/useByteStream";
import { TOPIC_UI_ACTIVITY, TOPIC_UI_ASSET, TOPIC_UI_STATE } from "@/lib/livekit";
import {
  initialUiStateStore,
  parseActivityEvent,
  parseUiStateMessage,
  uiStateReducer,
  type UiStateStore,
} from "@/lib/ui-state";

/** Delay before asking for the very first snapshot, in case one is in flight. */
const INITIAL_SNAPSHOT_DELAY_MS = 600;

export interface UseUiStateReturn {
  /** The envelope, with every optional slot filled in. */
  state: UiStateStore["state"];
  /** Last applied `seq` (`0` before the first snapshot). */
  seq: number;
  /** `asset_id` → object URL for bytes received on `lkap.ui.asset`. */
  assets: Map<string, string>;
  /** The state channel is live: connected and at least one snapshot applied. */
  connected: boolean;
  /** A gap was seen and a snapshot has been requested. */
  needsSnapshot: boolean;
  /** `session_id` carried by the stream we are following. */
  sessionId: string | null;
}

/** A text stream is complete when we have all the bytes its header promised. */
function isStreamComplete(text: string, size: number | undefined): boolean {
  if (size === undefined) return true;
  return new TextEncoder().encode(text).byteLength >= size;
}

function coerceSnapshot(payload: unknown): UiSnapshot | null {
  if (payload === undefined || payload === null) return null;
  let raw: string;
  try {
    raw = JSON.stringify(payload);
  } catch {
    return null;
  }
  const message = parseUiStateMessage(raw);
  return message?.type === "snapshot" ? message : null;
}

export function useUiState(sessionId: string | null): UseUiStateReturn {
  const session = useSessionContext();
  const room = session.room;
  const { perform, ready } = useAgentRpc();

  const [store, dispatch] = useReducer(
    uiStateReducer,
    undefined,
    initialUiStateStore,
  );

  const { textStreams: stateStreams } = useTextStream(TOPIC_UI_STATE, { room });
  const { textStreams: activityStreams } = useTextStream(TOPIC_UI_ACTIVITY, {
    room,
  });

  // Streams already folded into the reducer. A stream that fails to parse is
  // deliberately left out so it is retried once more chunks arrive.
  const appliedStateIds = useRef<Set<string>>(new Set());
  const appliedActivityIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    for (const stream of stateStreams) {
      const id = stream.streamInfo.id;
      if (appliedStateIds.current.has(id)) continue;
      if (!isStreamComplete(stream.text, stream.streamInfo.size)) continue;
      const message = parseUiStateMessage(stream.text);
      if (!message) continue;
      appliedStateIds.current.add(id);
      if (sessionId !== null && message.session_id !== sessionId) continue;
      dispatch({ type: "message", message });
    }
  }, [stateStreams, sessionId]);

  useEffect(() => {
    for (const stream of activityStreams) {
      const id = stream.streamInfo.id;
      if (appliedActivityIds.current.has(id)) continue;
      if (!isStreamComplete(stream.text, stream.streamInfo.size)) continue;
      const event = parseActivityEvent(stream.text);
      if (!event) continue;
      appliedActivityIds.current.add(id);
      dispatch({ type: "activity", event });
    }
  }, [activityStreams]);

  /* ----------------------------- assets --------------------------------- */

  const [assets, setAssets] = useState<Map<string, string>>(() => new Map());
  const assetUrls = useRef<Map<string, string>>(new Map());

  const onAsset = useCallback(
    (info: ByteStreamAssetInfo, bytes: Uint8Array) => {
      const attributes = info.attributes;
      if (
        sessionId !== null &&
        attributes.session_id !== undefined &&
        attributes.session_id !== sessionId
      ) {
        return;
      }
      const assetId = attributes.asset_id ?? info.name.split(".")[0];
      if (!assetId) return;
      const mime =
        attributes.mime || info.mimeType || "application/octet-stream";
      const url = URL.createObjectURL(
        new Blob([bytes as BlobPart], { type: mime }),
      );
      const previous = assetUrls.current.get(assetId);
      assetUrls.current.set(assetId, url);
      if (previous) URL.revokeObjectURL(previous);
      setAssets(new Map(assetUrls.current));
    },
    [sessionId],
  );

  useByteStream(TOPIC_UI_ASSET, onAsset, { room });

  useEffect(() => {
    const urls = assetUrls.current;
    return () => {
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  /* ------------------------- snapshot recovery -------------------------- */

  const requestToken = useRef<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    const wantSnapshot = store.needsSnapshot || store.seq === 0;
    if (!wantSnapshot) {
      requestToken.current = null;
      return;
    }
    const token = `${store.seq}:${store.needsSnapshot}`;
    if (requestToken.current === token) return;
    requestToken.current = token;

    let cancelled = false;
    const timer = setTimeout(
      () => {
        void perform({ v: 1, action: "get_snapshot", payload: {} }).then(
          (result) => {
            if (cancelled || !result.ok) return;
            const snapshot = coerceSnapshot(result.payload);
            if (snapshot) dispatch({ type: "message", message: snapshot });
          },
        );
      },
      store.needsSnapshot ? 0 : INITIAL_SNAPSHOT_DELAY_MS,
    );

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ready, perform, store.needsSnapshot, store.seq]);

  return useMemo(
    () => ({
      state: store.state,
      seq: store.seq,
      assets,
      connected: session.isConnected && store.seq > 0,
      needsSnapshot: store.needsSnapshot,
      sessionId: store.sessionId,
    }),
    [store, assets, session.isConnected],
  );
}
