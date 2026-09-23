"use client";

/**
 * `/s/[slug]?embed=1` (V2-18, UI_UX_SPEC-V2-AMENDMENTS §2.6): "no top strip,
 * compact control bar, 100% height inside the iframe, `postMessage` state
 * events. Text mode: transcript-first layout, composer pinned, no mic
 * controls."
 *
 * `channel=text` gets a purpose-built compact layout (`TextChat`, this
 * package). The default (voice) embed reuses `SessionExperience` as-is
 * inside a full-height wrapper rather than a hand-rolled voice UI, passing
 * `embed` so the live call drops its top strip and uses the compact control
 * bar (ask V2-18-9, closed by V2-19C). This keeps the voice path on WP-8's
 * avatar/track rendering instead of duplicating it.
 */
import * as React from "react";
import { useEffect } from "react";
import { SessionExperience } from "@/components/session/session-experience";
import { SessionUnavailable } from "@/components/session/session-unavailable";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import type { ConnectErrorKind } from "@/lib/livekit";

import { postToEmbedParent } from "./embed-bridge";
import { TextSessionView } from "./text-session-view";

export interface EmbedSessionProps {
  slug: string;
  agent: AgentPublicOut | null;
  loadError: string | null;
  loadErrorKind: ConnectErrorKind | null;
  /** `?channel=text`: the widget's text mode, or the console Test chat dialog's own iframe (unused there — see `test-chat/`). */
  channel: "text" | "voice";
  testMode: boolean;
}

export default function EmbedSession({
  slug,
  agent,
  loadError,
  loadErrorKind,
  channel,
  testMode,
}: EmbedSessionProps) {
  useEffect(() => {
    postToEmbedParent({ type: "state", state: "connecting" });
    return () => postToEmbedParent({ type: "state", state: "ended" });
  }, []);

  if (!agent) {
    const kind = loadErrorKind === "not_found" || loadErrorKind === "not_published" ? loadErrorKind : "unreachable";
    return <SessionUnavailable kind={kind} slug={slug} testMode={testMode} detail={loadError} />;
  }

  if (channel === "text") {
    return (
      <div data-embed="1" className="flex h-dvh w-full flex-col overflow-hidden">
        {/* `editable` is omitted (defaults false): §2.6 gives the widget a plain
            composer, no per-turn Edit/Replay (that is the console Test chat dialog's job). */}
        <TextSessionView
          slug={slug}
          viaConsole={testMode}
          fallbackAgentName={agent.name}
          onConnected={() => postToEmbedParent({ type: "state", state: "connected" })}
        />
      </div>
    );
  }

  return (
    <div data-embed="1" className="h-dvh w-full overflow-hidden">
      <SessionExperience
        slug={slug}
        agent={agent}
        loadError={loadError}
        loadErrorKind={loadErrorKind}
        testMode={testMode}
        embed
      />
    </div>
  );
}
