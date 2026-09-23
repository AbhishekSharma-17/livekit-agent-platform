"use client";

/**
 * The text-channel session: connects (via `createTextSessionTokenSource`,
 * `channel="text"`), and exposes the transcript, the composer's `send`, and
 * the `rewind`/`inject_user_text` `AgentAction`s (CONTRACTS-V2 §3.4).
 *
 * Two layers, because `useSessionMessages`/`useChat`/`useAgentRpc` all need
 * `SessionContext`/`RoomContext`, which only exist *inside*
 * `AgentSessionProvider` (the same shape `LiveSession` → `SessionRoom` already
 * uses for the voice session page): `useCreateTextSession` builds the
 * `AgentSession` and owns its lifecycle (mirrors `LiveSession`'s effect
 * exactly), and `useTextSessionMessaging` — called from a component rendered
 * *inside* `<AgentSessionProvider session={session}>` — reads the transcript
 * and performs actions on it.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "@livekit/components-react";
import { Room } from "livekit-client";

import type { ConnectResponse } from "@/contracts/lkap-contracts";
import { useAgentRpc } from "@/hooks/useAgentRpc";
import { describeConnectError } from "@/lib/livekit";

import { createTextSessionTokenSource } from "./text-token-source";

export interface UseCreateTextSessionOptions {
  slug: string;
  participantName?: string;
  /** `?mode=test` / the console Test chat dialog: route through the admin proxy (DECISIONS-W2 D-W2-1). */
  viaConsole?: boolean;
}

export interface UseCreateTextSessionReturn {
  session: ReturnType<typeof useSession>;
  details: ConnectResponse | null;
  error: string | null;
}

/** Owns one text-session attempt's `AgentSession` lifecycle (mirrors `LiveSession`). */
export function useCreateTextSession({
  slug,
  participantName = "Guest",
  viaConsole = false,
}: UseCreateTextSessionOptions): UseCreateTextSessionReturn {
  const [details, setDetails] = useState<ConnectResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [room] = useState(() => new Room());

  const { tokenSource, freeze } = useMemo(
    () =>
      createTextSessionTokenSource(
        slug,
        {
          onDetails: (response) => {
            setDetails((previous) => previous ?? response);
            setError(null);
          },
          onError: (cause) => setError(describeConnectError(cause)),
        },
        { viaConsole },
      ),
    [slug, viaConsole],
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
      .start({ signal: controller.signal })
      .catch((cause: unknown) => {
        if (!cancelled && !controller.signal.aborted) setError(describeConnectError(cause));
      });

    return () => {
      cancelled = true;
      controller.abort();
      freezeRef.current();
      void sessionRef.current.end();
    };
    // Runs once per attempt, exactly like `LiveSession`'s effect.
  }, []);

  return { session, details, error };
}

export interface RewindResult {
  ok: boolean;
  error?: string | null;
}

export interface UseTextSessionActions {
  /** Replay turn `turnIndex` unchanged: drop everything after it, regenerate. */
  rewind: (turnIndex: number) => Promise<RewindResult>;
  /**
   * Edit turn `turnIndex`'s text and regenerate in one call — truncates to
   * `turnIndex - 1` and appends `text` atomically (`text_mode.inject_user_text`
   * with `turn_index` set), so exactly one reply is produced.
   */
  editTurn: (turnIndex: number, text: string) => Promise<RewindResult>;
}

/** Call from *inside* `<AgentSessionProvider session={session}>` (see the module docstring). */
export function useTextSessionActions(): UseTextSessionActions {
  const { perform } = useAgentRpc();

  return {
    rewind: async (turnIndex: number) => {
      const result = await perform({ v: 1, action: "rewind", payload: { turn_index: turnIndex } });
      return { ok: result.ok, error: result.error };
    },
    editTurn: async (turnIndex: number, text: string) => {
      const result = await perform({
        v: 1,
        action: "inject_user_text",
        payload: { text, turn_index: turnIndex - 1 },
      });
      return { ok: result.ok, error: result.error };
    },
  };
}
