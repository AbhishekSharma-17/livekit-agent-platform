"use client";

/**
 * Client shell for `/s/[slug]`: pre-call card → live call → end-of-call card,
 * plus the three "unavailable" pages (docs/UI_UX_SPEC.md §5.1, §5.6, §5.7).
 *
 * `LiveSession` is mounted only while a call is running and is keyed by an
 * attempt counter, so "try again" always produces a fresh `TokenSource`, a
 * fresh room and a fresh `sessionId` rather than replaying a cached token for
 * a room the agent has already closed.
 *
 * §5.2 — the Start click is the browser's user gesture, so this is where the
 * `AudioContext` is created and resumed (synchronously, before any `await`);
 * it is handed to the attempt's room so LiveKit mixes playback through an
 * already-unblocked context.
 */
import * as React from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { LiveSession } from "@/components/session/live-session";
import { PreCallCard } from "@/components/session/pre-call-card";
import { EndOfCallCard } from "@/components/session/end-of-call-card";
import {
  SessionUnavailable,
  type SessionUnavailableKind,
} from "@/components/session/session-unavailable";
import { useMicCheck } from "@/hooks/use-mic-check";
import type { ConnectErrorKind } from "@/lib/livekit";

export interface SessionExperienceProps {
  slug: string;
  /** `null` when the agent lookup failed (unpublished, missing, down). */
  agent: AgentPublicOut | null;
  /** `classifyConnectError` of the server-side load failure. */
  loadErrorKind: ConnectErrorKind | null;
  /** The API's own message, surfaced to builders in test mode only. */
  loadError: string | null;
  /**
   * `?mode=test` (DECISIONS-W2 D-W2-1): the agent card came from the admin
   * proxy and the connect call below is routed the same way, so draft agents
   * can be called from the console's "Test call" link.
   */
  testMode: boolean;
  /** `NEXT_PUBLIC_LKAP_PRIVACY_URL`, when the deployment sets one. */
  privacyUrl?: string;
}

/** Create + resume the playback context inside the click (§5.2). */
function primeAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!Ctor) return null;
  try {
    const context = new Ctor();
    void context.resume();
    return context;
  } catch {
    return null;
  }
}

export function SessionExperience({
  slug,
  agent,
  loadErrorKind,
  loadError,
  testMode,
  privacyUrl,
}: SessionExperienceProps) {
  const [participantName, setParticipantName] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [endedMs, setEndedMs] = useState<number | null>(null);
  const [audioContext, setAudioContext] = useState<AudioContext | null>(null);
  const [audioDeviceId, setAudioDeviceId] = useState<string | undefined>(
    undefined,
  );
  /** The reason the last attempt was abandoned, shown above Start (§5.1). */
  const [attemptError, setAttemptError] = useState<string | null>(null);
  const mic = useMicCheck();

  /**
   * The primed context is owned *here*, not by `LiveSession`: an attempt's
   * room keeps a reference to it, and closing it from a mount-scoped effect
   * would kill it under React StrictMode (`next dev` runs setup → cleanup →
   * setup) and hand the next attempt a closed context. A closed context is
   * accepted verbatim by `Room.acquireAudioContext`, so the call would go
   * silent. Every entry point that starts a call primes a fresh one; the old
   * one is closed only once it is out of use.
   */
  const audioContextRef = useRef<AudioContext | null>(null);

  const closeAudioContext = useCallback((keep?: AudioContext | null) => {
    const current = audioContextRef.current;
    if (current && current !== keep && current.state !== "closed") {
      void current.close();
    }
    audioContextRef.current = keep ?? null;
  }, []);

  useEffect(() => () => closeAudioContext(null), [closeAudioContext]);

  const goLive = useCallback(
    (context: AudioContext | null, deviceId?: string) => {
      closeAudioContext(context);
      setAudioContext(context);
      setAudioDeviceId(deviceId);
      setEndedMs(null);
      setAttempt((value) => value + 1);
      setLive(true);
    },
    [closeAudioContext],
  );

  const start = useCallback(() => {
    setAttemptError(null);
    // Synchronous: this call *is* the user gesture.
    const context = primeAudioContext();
    void (async () => {
      if (mic.status === "idle" || mic.status === "denied") {
        const granted = await mic.request(mic.selectedId);
        if (!granted) {
          if (context && context.state !== "closed") void context.close();
          return;
        }
      }
      // Release the check's tracks before the room publishes its own.
      const deviceId = mic.selectedId;
      mic.stop();
      goLive(context, deviceId);
    })();
  }, [goLive, mic]);

  // "Try again" is a click, so it may prime a fresh context — and it must:
  // the previous attempt's context belongs to a room that is going away.
  const retry = useCallback(() => {
    setAttemptError(null);
    goLive(primeAudioContext(), audioDeviceId);
  }, [audioDeviceId, goLive]);

  const handleEnded = useCallback(
    (durationMs: number) => {
      setLive(false);
      setEndedMs(durationMs);
      closeAudioContext(null);
      setAudioContext(null);
    },
    [closeAudioContext],
  );

  const handleLeave = useCallback(
    (reason?: string | null) => {
      setLive(false);
      setEndedMs(null);
      setAttemptError(reason ?? null);
      closeAudioContext(null);
      setAudioContext(null);
    },
    [closeAudioContext],
  );

  const backHref = agent ? `/console/agents/${agent.id}` : undefined;

  if (!agent) {
    const kind: SessionUnavailableKind =
      loadErrorKind === "not_found" || loadErrorKind === "not_published"
        ? loadErrorKind
        : "unreachable";
    return (
      <SessionUnavailable
        kind={kind}
        slug={slug}
        testMode={testMode}
        detail={loadError}
      />
    );
  }

  if (live) {
    return (
      <LiveSession
        key={attempt}
        slug={slug}
        agent={agent}
        participantName={participantName.trim() || "Guest"}
        testMode={testMode}
        audioContext={audioContext}
        audioDeviceId={audioDeviceId}
        onRetry={retry}
        onLeave={handleLeave}
        onEnded={handleEnded}
      />
    );
  }

  if (endedMs !== null) {
    return (
      <EndOfCallCard
        agentName={agent.name}
        durationMs={endedMs}
        onRestart={() => goLive(primeAudioContext(), audioDeviceId)}
        testMode={testMode}
        backHref={backHref}
      />
    );
  }

  return (
    <PreCallCard
      agent={agent}
      participantName={participantName}
      onParticipantNameChange={setParticipantName}
      onStart={start}
      devices={{
        status: mic.status,
        level: mic.level,
        inputs: mic.inputs,
        selectedId: mic.selectedId,
      }}
      onCheckMicrophone={() => void mic.request(mic.selectedId)}
      onSelectInput={mic.select}
      error={attemptError ?? loadError}
      testMode={testMode}
      backHref={backHref}
      privacyUrl={privacyUrl}
    />
  );
}
