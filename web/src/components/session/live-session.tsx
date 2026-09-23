"use client";

/**
 * Owns the LiveKit `useSession` lifecycle for one call attempt.
 *
 * Mounted only once the visitor asks to start, because `useSession` calls the
 * token source (→ `POST /connect`, which creates a session row) from
 * `prepareConnection()` as soon as it mounts. The parent remounts this with a
 * fresh `key` to retry, so every attempt gets its own `TokenSource` and room.
 *
 * Audio priming (docs/UI_UX_SPEC.md §5.2): the Start click — the browser's
 * user gesture — created and resumed an `AudioContext`, which is handed to
 * this attempt's `Room` as `webAudioMix.audioContext`. LiveKit then mixes
 * playback through that already-unblocked context instead of hoping a fresh
 * one may start. The same `Room` carries the microphone the visitor picked in
 * the device check (`audioCaptureDefaults.deviceId`), which is how the choice
 * reaches the published track without touching the vendored control bar's
 * persisted user choices.
 *
 * Because `webAudioMix` is given as an object, `livekit-client` deliberately
 * does *not* close the context on disconnect. `SessionExperience` owns it and
 * closes it when the call is over: closing it here (from a mount-scoped
 * effect) would kill the context under React StrictMode and hand the next
 * attempt a closed one, which `Room` accepts without complaint — a silent
 * call.
 */
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "@livekit/components-react";
import { Room } from "livekit-client";

import type { AgentPublicOut, ConnectResponse } from "@/contracts/lkap-contracts";
import { AgentSessionProvider } from "@/components/agents-ui/agent-session-provider";
import { SessionRoom } from "@/components/session/session-room";
import {
  ConnectError,
  createConnectTokenSource,
  describeConnectError,
} from "@/lib/livekit";

export interface LiveSessionProps {
  slug: string;
  /** Agent shown until the connect response supplies a fresher copy. */
  agent: AgentPublicOut;
  participantName: string;
  /**
   * `?mode=test` (DECISIONS-W2 D-W2-1): route the connect call through the
   * console's admin proxy instead of the public API.
   */
  testMode: boolean;
  /** Primed in the Start click (§5.2) and owned by `SessionExperience`. */
  audioContext?: AudioContext | null;
  /** The microphone chosen in the pre-call device check, if any. */
  audioDeviceId?: string;
  onRetry: () => void;
  onLeave: (reason?: string | null) => void;
  onEnded: (durationMs: number) => void;
}

export function LiveSession({
  slug,
  agent,
  participantName,
  testMode,
  audioContext,
  audioDeviceId,
  onRetry,
  onLeave,
  onEnded,
}: LiveSessionProps) {
  const [details, setDetails] = useState<ConnectResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // One room per attempt, built lazily so the primed context is attached
  // before `useSession` ever connects.
  const [room] = useState(
    () =>
      new Room({
        ...(audioContext ? { webAudioMix: { audioContext } } : {}),
        ...(audioDeviceId
          ? { audioCaptureDefaults: { deviceId: audioDeviceId } }
          : {}),
      }),
  );

  const { tokenSource, freeze } = useMemo(
    () =>
      createConnectTokenSource(
        slug,
        {
          // First response wins: one attempt is one `sessionId`.
          onDetails: (response) => {
            setDetails((previous) => previous ?? response);
            setError(null);
          },
          onError: (cause) => {
            // In test mode the connect call goes through the console's admin
            // proxy, so a 401/403 there can only mean the proxy's
            // `LKAP_ADMIN_TOKEN` is rejected by the api — not that the agent
            // is unpublished (DECISIONS-W2 D-W2-1 item 5).
            if (
              testMode &&
              cause instanceof ConnectError &&
              (cause.status === 401 || cause.status === 403)
            ) {
              setError("The console's admin token is not accepted by the API.");
              return;
            }
            setError(describeConnectError(cause));
          },
        },
        { viaConsole: testMode },
      ),
    [slug, testMode],
  );

  const session = useSession(tokenSource, { participantName, room });
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const freezeRef = useRef(freeze);
  freezeRef.current = freeze;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    sessionRef.current
      .start({
        signal: controller.signal,
        tracks: {
          microphone: { enabled: true, publishOptions: { preConnectBuffer: true } },
        },
      })
      .catch((cause: unknown) => {
        if (!cancelled && !controller.signal.aborted) {
          setError(describeConnectError(cause));
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
      // Freeze before ending: `end()` force-refetches the token, which would
      // otherwise open a second session against the API on every hangup.
      freezeRef.current();
      void sessionRef.current.end();
    };
    // Runs once per attempt: the parent remounts this component to reconnect,
    // and `sessionRef` keeps the effect free of render-to-render identities.
  }, []);

  return (
    <AgentSessionProvider session={session}>
      <SessionRoom
        agent={details?.agent ?? agent}
        sessionId={details?.sessionId ?? null}
        uiPanelId={details?.uiPanelId ?? null}
        error={error}
        testMode={testMode}
        onRetry={onRetry}
        onLeave={onLeave}
        onEnded={onEnded}
      />
    </AgentSessionProvider>
  );
}
