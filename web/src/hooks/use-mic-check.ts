"use client";

/**
 * `useMicCheck` — the pre-call device check of docs/UI_UX_SPEC.md §5.1.
 *
 * Permission is **never** requested on page load: only `request()` (the
 * "Check microphone" button, or "Start call" when the check has not run yet)
 * calls `getUserMedia`. While a stream is held the hook drives a 0–1 level
 * from an `AnalyserNode` so the `StateMeter` can act as a live level meter,
 * and `stop()` releases the tracks *before* the room starts publishing so the
 * two never fight over the device.
 *
 * The hook only produces the `devices` object `PreCallCard` takes as a prop —
 * the card itself is injectable and renders every state without a browser.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { MicCheckStatus } from "@/components/session/session-state";

/** The injectable shape `PreCallCard` renders (WP-8 card item 1). */
export interface MicDevices {
  status: MicCheckStatus;
  /** 0–1 input level while `status === "granted"`. */
  level: number;
  inputs: MediaDeviceInfo[];
  selectedId?: string;
}

export interface MicCheck extends MicDevices {
  /** Ask for permission (and, with `deviceId`, for a specific input). */
  request: (deviceId?: string) => Promise<boolean>;
  /** Switch input; re-acquires the stream when permission is already granted. */
  select: (deviceId: string) => void;
  /** Release the microphone (called before the room publishes). */
  stop: () => void;
}

function supported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

function statusForError(error: unknown): MicCheckStatus {
  const name =
    typeof error === "object" && error !== null && "name" in error
      ? String((error as { name: unknown }).name)
      : "";
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "unsupported";
  }
  return "denied";
}

export function useMicCheck(): MicCheck {
  const [status, setStatus] = useState<MicCheckStatus>("idle");
  const [level, setLevel] = useState(0);
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const frameRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const release = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const context = contextRef.current;
    contextRef.current = null;
    if (context && context.state !== "closed") void context.close();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      release();
    };
  }, [release]);

  /** Drive `level` from the live stream. Silently skipped without WebAudio. */
  const meter = useCallback((stream: MediaStream) => {
    const Ctor =
      typeof window === "undefined"
        ? undefined
        : (window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext })
            .webkitAudioContext);
    if (!Ctor) return;
    let context: AudioContext;
    try {
      context = new Ctor();
      contextRef.current = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const value of data) {
          const centred = (value - 128) / 128;
          sum += centred * centred;
        }
        const rms = Math.sqrt(sum / data.length);
        // Speech RMS rarely exceeds ~0.3; scale so normal talking fills the bars.
        if (mountedRef.current) setLevel(Math.min(1, rms * 3.2));
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch {
      // No metering: the status line still reports "Microphone works".
    }
  }, []);

  const request = useCallback(
    async (deviceId?: string): Promise<boolean> => {
      if (!supported()) {
        setStatus("unsupported");
        return false;
      }
      setStatus("requesting");
      release();
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true,
        });
        if (!mountedRef.current) {
          stream.getTracks().forEach((track) => track.stop());
          return false;
        }
        streamRef.current = stream;
        const active =
          deviceId ?? stream.getAudioTracks()[0]?.getSettings?.().deviceId;
        setSelectedId(active ?? undefined);
        setStatus("granted");
        meter(stream);
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          if (mountedRef.current) {
            setInputs(
              all.filter(
                (device) =>
                  device.kind === "audioinput" && device.deviceId !== "",
              ),
            );
          }
        } catch {
          // Device labels are a nicety; permission is what matters.
        }
        return true;
      } catch (error) {
        if (mountedRef.current) {
          setStatus(statusForError(error));
          setLevel(0);
        }
        return false;
      }
    },
    [meter, release],
  );

  const select = useCallback(
    (deviceId: string) => {
      setSelectedId(deviceId);
      if (streamRef.current) void request(deviceId);
    },
    [request],
  );

  const stop = useCallback(() => {
    release();
    setLevel(0);
  }, [release]);

  return { status, level, inputs, selectedId, request, select, stop };
}
