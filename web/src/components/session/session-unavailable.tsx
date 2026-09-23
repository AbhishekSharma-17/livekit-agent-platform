"use client";

/**
 * The three "this link doesn't work" pages of docs/UI_UX_SPEC.md §5.7, keyed
 * by `classifyConnectError` (`lib/livekit.ts`). Same card shell as the
 * pre-call and end-of-call screens; the copy never blames the visitor and
 * only `not_published` offers the builder a way into the console.
 */
import * as React from "react";

import { StateMeter } from "@/components/shared/state-meter";
import { Button } from "@/components/ui/button";
import {
  SessionCard,
  SessionCardScreen,
} from "@/components/session/session-card";

export type SessionUnavailableKind =
  | "not_found"
  | "not_published"
  | "unreachable";

export interface SessionUnavailableProps {
  kind: SessionUnavailableKind;
  slug: string;
  /** `?mode=test`: the call came from the console, so the way back is there. */
  testMode?: boolean;
  /**
   * The API's own message. Only surfaced in test mode (a builder can act on
   * "the console's admin token is not accepted"; a claimant cannot).
   */
  detail?: string | null;
}

const COPY: Record<
  SessionUnavailableKind,
  { title: string; body: string }
> = {
  not_found: {
    title: "There's no agent at this address",
    body: "Check the link you were given, or ask whoever sent it for a new one.",
  },
  not_published: {
    title: "This agent isn't live yet",
    body: "It exists, but it hasn't been published. If you're setting it up, open it in test mode from the console.",
  },
  unreachable: {
    title: "We couldn't reach the service",
    body: "Try again in a moment.",
  },
};

export function SessionUnavailable({
  kind,
  slug,
  testMode,
  detail,
}: SessionUnavailableProps) {
  const copy = COPY[kind];

  return (
    <SessionCardScreen>
      <SessionCard data-testid="session-unavailable">
        <div
          data-kind={kind}
          className="flex flex-col items-center gap-4 p-6 text-center"
        >
          <StateMeter state={kind === "unreachable" ? "failed" : "ended"} size="lg" bars={5} />
          <div>
            <h1 className="text-[1.75rem] leading-[2.125rem] font-semibold tracking-[-0.02em] text-balance">
              {copy.title}
            </h1>
            <p className="text-muted-foreground mt-2 text-base text-pretty">
              {copy.body}
            </p>
          </div>

          {kind === "not_published" && (
            <Button variant="brand" size="lg" asChild>
              <a href={`/console/agents?q=${encodeURIComponent(slug)}`}>
                Open in console
              </a>
            </Button>
          )}
          {kind === "unreachable" && (
            <Button
              variant="brand"
              size="lg"
              onClick={() => window.location.reload()}
            >
              Try again
            </Button>
          )}

          {testMode && detail && (
            <p className="text-muted-foreground font-mono text-[0.8125rem]">
              {detail}
            </p>
          )}
        </div>
      </SessionCard>
      <p className="text-muted-foreground text-sm">
        <a
          href={testMode ? "/console/agents" : "/"}
          className="underline underline-offset-4"
        >
          {testMode ? "Back to console" : "Home"}
        </a>
      </p>
    </SessionCardScreen>
  );
}
