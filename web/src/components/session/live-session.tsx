"use client";

/**
 * Owns the LiveKit `useSession` lifecycle for one call attempt.
 *
 * Mounted only once the visitor asks to start, because `useSession` calls the
 * token source (→ `POST /connect`, which creates a session row) from
 * `prepareConnection()` as soon as it mounts. The parent remounts this with a
 * fresh `key` to retry, so every attempt gets its own `TokenSource` and room.
 */
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "@livekit/components-react";

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
  onRetry: () => void;
  onEnded: () => void;
}

export function LiveSession({
  slug,
  agent,
  participantName,
  testMode,
  onRetry,
  onEnded,
}: LiveSessionProps) {
  const [details, setDetails] = useState<ConnectResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const session = useSession(tokenSource, { participantName });
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
        onRetry={onRetry}
        onEnded={onEnded}
      />
    </AgentSessionProvider>
  );
}
