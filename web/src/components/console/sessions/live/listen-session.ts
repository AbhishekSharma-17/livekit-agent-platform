"use client";

/**
 * V5-38 (`docs/v5/_asks.md` #251): the Live tab's own LiveKit connection —
 * a hidden, subscribe-only listener joining the session's room through the
 * same `useSession` machinery the visitor-facing call uses
 * (`components/session/live-session.tsx`), but with a `POST
 * .../listen-token` token source instead of `POST .../connect`, and every
 * local track (mic, camera, screen) held off — the api's token already sets
 * `canPublishData=false`; a hidden participant can't publish audio/video/data
 * or call an RPC either way (#250 item 2).
 *
 * The token lasts 15 minutes (`SessionListenTokenOut.expiresAt`). LiveKit's
 * `TokenSource.custom` caches the credentials it returns until they're near
 * expiry, so `useSession` only calls `fetchSessionListenToken` again on an
 * unexpected drop — which is exactly what happens once the token's `exp`
 * passes (the server disconnects the participant). Rather than silently
 * reconnecting behind the supervisor's back, this surfaces that as a plain
 * "reconnect" affordance (`phase: "disconnected"`) once it has happened, plus
 * an earlier heads-up (`expiresInMs`) while the token is still good.
 */
import * as React from "react";
import { useSession } from "@livekit/components-react";
import { Room, TokenSource } from "livekit-client";

import { fetchSessionListenToken } from "@/components/console/lib/api-hooks";
import { ApiError } from "@/lib/api";
import { toPanelConnectionState } from "@/lib/livekit";

export type ListenSessionPhase =
  /** Not connected yet — the first token/connect round trip is in flight. */
  | "connecting"
  /** Connected and receiving audio. */
  | "live"
  /** A transient network hiccup; LiveKit is retrying on its own. */
  | "reconnecting"
  /** Was connected, then dropped (most likely the 15-minute token expiring) — needs a manual reconnect. */
  | "disconnected"
  /** `POST .../listen-token` answered 409 `not_live`: the session isn't active any more. */
  | "not_live"
  /** Any other failure minting the token (network, 4xx/5xx). */
  | "error";

export interface UseListenSessionReturn {
  session: ReturnType<typeof useSession>;
  phase: ListenSessionPhase;
  /** Set for `phase === "error"` (and, for context, `"not_live"`). */
  errorMessage: string | null;
  /** `SessionListenTokenOut.expiresAt` of the token currently in use, or `null` before the first mint. */
  expiresAt: string | null;
  /** Milliseconds until `expiresAt`; `null` when unknown. Recomputed every second. */
  expiresInMs: number | null;
  /** Mint a fresh token and reconnect — the `phase === "disconnected"` affordance. */
  reconnect: () => void;
}

/** Below this, the tab shows "expires soon" (comfortably inside one polling/UI tick of the mark). */
export const EXPIRY_WARNING_MS = 90_000;

export function useListenSession(sessionId: string): UseListenSessionReturn {
  const [expiresAt, setExpiresAt] = React.useState<string | null>(null);
  const [mintError, setMintError] = React.useState<{ notLive: boolean; message: string } | null>(null);
  const [now, setNow] = React.useState(() => Date.now());

  const [room] = React.useState(() => new Room());

  const tokenSource = React.useMemo(
    () =>
      TokenSource.custom(async () => {
        try {
          const details = await fetchSessionListenToken(sessionId);
          setExpiresAt(details.expiresAt);
          setMintError(null);
          return { serverUrl: details.serverUrl, participantToken: details.participantToken };
        } catch (cause) {
          setMintError({
            notLive: cause instanceof ApiError && cause.code === "not_live",
            message: cause instanceof ApiError ? cause.message : "Couldn't reach the API.",
          });
          throw cause;
        }
      }),
    [sessionId],
  );

  const session = useSession(tokenSource, { room });
  const sessionRef = React.useRef(session);
  sessionRef.current = session;

  const start = React.useCallback((signal?: AbortSignal) => {
    // Hidden and listen-only: no mic, camera or screen share published from
    // this tab, whatever `useSession`'s own default would otherwise enable.
    return sessionRef.current.start({
      signal,
      tracks: { microphone: { enabled: false }, camera: { enabled: false }, screenShare: { enabled: false } },
    });
  }, []);

  // Mint + connect once per session id (the acceptance test: "requests the
  // token once and connects").
  React.useEffect(() => {
    const controller = new AbortController();
    start(controller.signal).catch(() => {
      // `mintError` (mint failures) or the room's own connection state
      // (a connect failure after a successful mint) already carry this.
    });
    return () => {
      controller.abort();
      void sessionRef.current.end();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `start` is stable; only `sessionId` should re-run this.
  }, [sessionId]);

  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const reconnect = React.useCallback(() => {
    setMintError(null);
    start().catch(() => {});
  }, [start]);

  const wasConnectedRef = React.useRef(false);
  const connectionState = toPanelConnectionState(session.connectionState);
  if (connectionState === "connected") wasConnectedRef.current = true;

  const phase: ListenSessionPhase = mintError?.notLive
    ? "not_live"
    : mintError
      ? "error"
      : connectionState === "connected"
        ? "live"
        : connectionState === "reconnecting"
          ? "reconnecting"
          : connectionState === "disconnected" && wasConnectedRef.current
            ? "disconnected"
            : "connecting";

  const expiresInMs = expiresAt ? new Date(expiresAt).getTime() - now : null;

  return {
    session,
    phase,
    errorMessage: mintError?.message ?? null,
    expiresAt,
    expiresInMs,
    reconnect,
  };
}
