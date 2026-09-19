"use client";

/**
 * `useByteStream` — CONTRACTS §10.
 *
 * `@livekit/components-react@2.9.24` ships no React hook for byte streams
 * (only `useTextStream`), so the `lkap.ui.asset` channel is read straight off
 * `room.registerByteStreamHandler` from `livekit-client`
 * (docs/ARCHITECTURE.md §9).
 */
import { useEffect, useRef } from "react";
import { useMaybeRoomContext } from "@livekit/components-react";
import type { ByteStreamReader, Room } from "livekit-client";

export interface ByteStreamAssetInfo {
  /** Stream attributes: `asset_id`, `kind`, `mime`, `caption`?, `session_id`. */
  attributes: Record<string, string>;
  /** Stream name, `{asset_id}.{ext}` (CONTRACTS §10). */
  name: string;
  mimeType: string;
  /** Identity of the participant that opened the stream. */
  senderIdentity: string;
}

export type ByteStreamAssetHandler = (
  info: ByteStreamAssetInfo,
  bytes: Uint8Array,
) => void;

export interface UseByteStreamOptions {
  /** Room to bind to; defaults to the surrounding `RoomContext`. */
  room?: Room;
  /** Called when a stream fails mid-read (the partial asset is dropped). */
  onError?: (error: unknown, info: ByteStreamAssetInfo) => void;
}

function concatChunks(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

/**
 * Subscribe to a byte-stream topic for the lifetime of the component.
 *
 * Only one handler may be registered per topic per room, so mount this once
 * per topic (`useUiState` owns `lkap.ui.asset` for the session page).
 */
export function useByteStream(
  topic: string,
  onAsset: ByteStreamAssetHandler,
  options: UseByteStreamOptions = {},
): void {
  const contextRoom = useMaybeRoomContext();
  const room = options.room ?? contextRoom;

  const onAssetRef = useRef(onAsset);
  onAssetRef.current = onAsset;
  const onErrorRef = useRef(options.onError);
  onErrorRef.current = options.onError;

  useEffect(() => {
    if (!room) return;

    let cancelled = false;

    const handler = (
      reader: ByteStreamReader,
      participantInfo: { identity: string },
    ) => {
      const info: ByteStreamAssetInfo = {
        attributes: reader.info.attributes ?? {},
        name: reader.info.name,
        mimeType: reader.info.mimeType,
        senderIdentity: participantInfo.identity,
      };
      void reader
        .readAll()
        .then((chunks) => {
          if (cancelled) return;
          onAssetRef.current(info, concatChunks(chunks));
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          onErrorRef.current?.(error, info);
        });
    };

    room.registerByteStreamHandler(topic, handler);
    return () => {
      cancelled = true;
      room.unregisterByteStreamHandler(topic);
    };
  }, [room, topic]);
}
