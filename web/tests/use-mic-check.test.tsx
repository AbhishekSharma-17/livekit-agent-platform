import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMicCheck } from "@/hooks/use-mic-check";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fakeStream(deviceId = "mic-a") {
  const stop = vi.fn();
  const track = {
    stop,
    kind: "audio",
    getSettings: () => ({ deviceId }),
  } as unknown as MediaStreamTrack;
  const stream = {
    getTracks: () => [track],
    getAudioTracks: () => [track],
  } as unknown as MediaStream;
  return { stream, stop };
}

function stubMediaDevices(
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>,
  devices: MediaDeviceInfo[] = [],
) {
  vi.stubGlobal("navigator", {
    userAgent: "test",
    mediaDevices: {
      getUserMedia,
      enumerateDevices: () => Promise.resolve(devices),
    },
  });
}

/**
 * UI_UX_SPEC §5.1 — permission is only ever requested by an explicit action,
 * and the tracks are released before the room publishes its own.
 */
describe("useMicCheck", () => {
  it("starts idle and does not touch getUserMedia on mount", () => {
    const getUserMedia = vi.fn();
    stubMediaDevices(getUserMedia as never);
    const { result } = renderHook(() => useMicCheck());
    expect(result.current.status).toBe("idle");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("reports granted and remembers the active device", async () => {
    const { stream } = fakeStream("mic-a");
    stubMediaDevices(
      () => Promise.resolve(stream),
      [
        { deviceId: "mic-a", label: "Built-in", kind: "audioinput" },
        { deviceId: "", label: "", kind: "audioinput" },
        { deviceId: "cam", label: "Camera", kind: "videoinput" },
      ] as unknown as MediaDeviceInfo[],
    );
    const { result } = renderHook(() => useMicCheck());

    await act(async () => {
      await result.current.request();
    });

    expect(result.current.status).toBe("granted");
    expect(result.current.selectedId).toBe("mic-a");
    await waitFor(() => expect(result.current.inputs).toHaveLength(1));
    expect(result.current.inputs[0].deviceId).toBe("mic-a");
  });

  it("reports a refusal as denied", async () => {
    const error = Object.assign(new Error("denied"), {
      name: "NotAllowedError",
    });
    stubMediaDevices(() => Promise.reject(error));
    const { result } = renderHook(() => useMicCheck());

    await act(async () => {
      expect(await result.current.request()).toBe(false);
    });
    expect(result.current.status).toBe("denied");
  });

  it("reports a missing device as unsupported", async () => {
    const error = Object.assign(new Error("none"), { name: "NotFoundError" });
    stubMediaDevices(() => Promise.reject(error));
    const { result } = renderHook(() => useMicCheck());

    await act(async () => {
      await result.current.request();
    });
    expect(result.current.status).toBe("unsupported");
  });

  it("reports a browser without getUserMedia as unsupported", async () => {
    vi.stubGlobal("navigator", { userAgent: "test", mediaDevices: undefined });
    const { result } = renderHook(() => useMicCheck());

    await act(async () => {
      expect(await result.current.request()).toBe(false);
    });
    expect(result.current.status).toBe("unsupported");
  });

  it("stops the tracks on stop() and on unmount", async () => {
    const first = fakeStream("mic-a");
    stubMediaDevices(() => Promise.resolve(first.stream));
    const { result, unmount } = renderHook(() => useMicCheck());

    await act(async () => {
      await result.current.request();
    });
    act(() => result.current.stop());
    expect(first.stop).toHaveBeenCalled();

    const second = fakeStream("mic-a");
    stubMediaDevices(() => Promise.resolve(second.stream));
    await act(async () => {
      await result.current.request();
    });
    unmount();
    expect(second.stop).toHaveBeenCalled();
  });

  it("re-acquires the stream when the visitor picks another input", async () => {
    const calls: MediaStreamConstraints[] = [];
    const { stream } = fakeStream("mic-a");
    stubMediaDevices((constraints) => {
      calls.push(constraints);
      return Promise.resolve(stream);
    });
    const { result } = renderHook(() => useMicCheck());

    await act(async () => {
      await result.current.request();
    });
    await act(async () => {
      result.current.select("mic-b");
    });

    expect(result.current.selectedId).toBe("mic-b");
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toEqual({ audio: { deviceId: { exact: "mic-b" } } });
  });
});
