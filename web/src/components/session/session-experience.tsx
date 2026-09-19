"use client";

/**
 * Client shell for `/s/[slug]`: pre-call card → live call → post-call card.
 *
 * `LiveSession` is mounted only while a call is running and is keyed by an
 * attempt counter, so "try again" always produces a fresh `TokenSource`, a
 * fresh room and a fresh `sessionId` rather than replaying a cached token for
 * a room the agent has already closed.
 */
import * as React from "react";
import { useCallback, useState } from "react";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { Button } from "@/components/ui/button";
import { LiveSession } from "@/components/session/live-session";
import { PreCallCard } from "@/components/session/pre-call-card";

export interface SessionExperienceProps {
  slug: string;
  /** `null` when the agent lookup failed (unpublished, missing, down). */
  agent: AgentPublicOut | null;
  loadError: string | null;
  /**
   * `?mode=test` (DECISIONS-W2 D-W2-1): the agent card came from the admin
   * proxy and the connect call below is routed the same way, so draft agents
   * can be called from the console's "Test call" link.
   */
  testMode: boolean;
}

export function SessionExperience({
  slug,
  agent,
  loadError,
  testMode,
}: SessionExperienceProps) {
  const [participantName, setParticipantName] = useState("Guest");
  const [attempt, setAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [ended, setEnded] = useState(false);

  const start = useCallback(() => {
    setEnded(false);
    setAttempt((value) => value + 1);
    setLive(true);
  }, []);

  const retry = useCallback(() => {
    setEnded(false);
    setAttempt((value) => value + 1);
    setLive(true);
  }, []);

  const handleEnded = useCallback(() => {
    setLive(false);
    setEnded(true);
  }, []);

  if (!agent) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-6">
        <div className="border-border/60 bg-card/40 w-full max-w-md rounded-2xl border p-6 text-center">
          <h1 className="text-xl font-semibold tracking-tight">
            This session is not available
          </h1>
          <p className="text-muted-foreground mt-2 text-sm">
            {loadError ?? `No agent is published at /s/${slug}.`}
          </p>
        </div>
      </main>
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
        onRetry={retry}
        onEnded={handleEnded}
      />
    );
  }

  if (ended) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-6">
        <div className="border-border/60 bg-card/40 w-full max-w-md rounded-2xl border p-6 text-center">
          <h1 className="text-xl font-semibold tracking-tight">Call ended</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            Thanks for talking with {agent.name}.
          </p>
          <Button className="mt-6" onClick={start}>
            Start a new call
          </Button>
        </div>
      </main>
    );
  }

  return (
    <PreCallCard
      agent={agent}
      participantName={participantName}
      onParticipantNameChange={setParticipantName}
      onStart={start}
      error={loadError}
      testMode={testMode}
    />
  );
}
