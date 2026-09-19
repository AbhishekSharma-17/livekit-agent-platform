import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ByteStreamReader, Room } from "livekit-client";

import {
  useByteStream,
  type ByteStreamAssetHandler,
} from "@/hooks/useByteStream";
import { TOPIC_UI_ASSET } from "@/lib/livekit";

type Handler = (
  reader: ByteStreamReader,
  participantInfo: { identity: string },
) => void;

function fakeRoom() {
  const handlers = new Map<string, Handler>();
  const room = {
    registerByteStreamHandler: vi.fn((topic: string, handler: Handler) => {
      if (handlers.has(topic)) {
        throw new Error(`byte stream handler already set for topic ${topic}`);
      }
      handlers.set(topic, handler);
    }),
    unregisterByteStreamHandler: vi.fn((topic: string) => {
      handlers.delete(topic);
    }),
  };
  return { room: room as unknown as Room, handlers, spies: room };
}

function fakeReader(
  attributes: Record<string, string>,
  chunks: Uint8Array[],
  options: { name?: string; mimeType?: string; fail?: boolean } = {},
): ByteStreamReader {
  return {
    info: {
      attributes,
      name: options.name ?? "asset-1.jpg",
      mimeType: options.mimeType ?? "image/jpeg",
    },
    readAll: async () => {
      if (options.fail) throw new Error("stream aborted");
      return chunks;
    },
  } as unknown as ByteStreamReader;
}

afterEach(() => vi.restoreAllMocks());

describe("useByteStream", () => {
  it("registers on mount and unregisters on unmount", () => {
    const { room, spies } = fakeRoom();
    const onAsset: ByteStreamAssetHandler = vi.fn();

    const { unmount } = renderHook(() =>
      useByteStream(TOPIC_UI_ASSET, onAsset, { room }),
    );

    expect(spies.registerByteStreamHandler).toHaveBeenCalledTimes(1);
    expect(spies.registerByteStreamHandler.mock.calls[0]?.[0]).toBe(
      TOPIC_UI_ASSET,
    );

    unmount();
    expect(spies.unregisterByteStreamHandler).toHaveBeenCalledWith(
      TOPIC_UI_ASSET,
    );
  });

  it("delivers concatenated bytes with the stream attributes", async () => {
    const { room, handlers } = fakeRoom();
    const onAsset = vi.fn();

    renderHook(() => useByteStream(TOPIC_UI_ASSET, onAsset, { room }));

    handlers.get(TOPIC_UI_ASSET)?.(
      fakeReader({ asset_id: "asset-1", kind: "photo", mime: "image/jpeg" }, [
        new Uint8Array([1, 2]),
        new Uint8Array([3]),
      ]),
      { identity: "agent-1" },
    );

    await waitFor(() => expect(onAsset).toHaveBeenCalledTimes(1));
    const [info, bytes] = onAsset.mock.calls[0] as [
      { attributes: Record<string, string>; name: string; mimeType: string },
      Uint8Array,
    ];
    expect(info.attributes.asset_id).toBe("asset-1");
    expect(info.name).toBe("asset-1.jpg");
    expect(info.mimeType).toBe("image/jpeg");
    expect(Array.from(bytes)).toEqual([1, 2, 3]);
  });

  it("reports a failed stream instead of delivering a partial asset", async () => {
    const { room, handlers } = fakeRoom();
    const onAsset = vi.fn();
    const onError = vi.fn();

    renderHook(() =>
      useByteStream(TOPIC_UI_ASSET, onAsset, { room, onError }),
    );

    handlers.get(TOPIC_UI_ASSET)?.(
      fakeReader({ asset_id: "asset-2" }, [], { fail: true }),
      { identity: "agent-1" },
    );

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onAsset).not.toHaveBeenCalled();
  });

  it("uses the latest callback without re-registering", async () => {
    const { room, handlers, spies } = fakeRoom();
    const first = vi.fn();
    const second = vi.fn();

    const { rerender } = renderHook(
      ({ handler }: { handler: ByteStreamAssetHandler }) =>
        useByteStream(TOPIC_UI_ASSET, handler, { room }),
      { initialProps: { handler: first as ByteStreamAssetHandler } },
    );

    rerender({ handler: second as ByteStreamAssetHandler });
    expect(spies.registerByteStreamHandler).toHaveBeenCalledTimes(1);

    handlers.get(TOPIC_UI_ASSET)?.(
      fakeReader({ asset_id: "asset-3" }, [new Uint8Array([9])]),
      { identity: "agent-1" },
    );

    await waitFor(() => expect(second).toHaveBeenCalledTimes(1));
    expect(first).not.toHaveBeenCalled();
  });

  it("does nothing without a room", () => {
    const onAsset = vi.fn();
    expect(() =>
      renderHook(() => useByteStream(TOPIC_UI_ASSET, onAsset)),
    ).not.toThrow();
  });
});
