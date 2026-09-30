"use client";

/**
 * A complete text-channel session: connects, then renders `TextChat` inside
 * the provider it needs (see `use-text-session.ts`'s module docstring for why
 * session-creation and `useTextSessionActions`/`TextChat` are split into two
 * components). Shared by the widget's embed text mode (`editable={false}`,
 * UI_UX_SPEC-V2-AMENDMENTS §2.6) and the console's **Test chat** dialog
 * (`editable`, per-turn Edit/Replay — `components/console/agents/test-chat/`).
 */
import { useEffect } from "react";
import { AgentSessionProvider } from "@/components/agents-ui/agent-session-provider";
import { LoadingRow } from "@/components/shared/loading-state";
import { Alert } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

import type { ConnectResponse } from "@/contracts/lkap-contracts";

import { TextChat } from "./text-chat";
import { useCreateTextSession, useTextSessionActions } from "./use-text-session";

export interface TextSessionViewProps {
  slug: string;
  /** `?mode=test` / the console Test chat dialog: route through the admin proxy (DECISIONS-W2 D-W2-1). */
  viaConsole?: boolean;
  /** Shown until the session's own `ConnectResponse.agent.name` arrives. */
  fallbackAgentName: string;
  /** Per-turn Edit/Replay controls (console Test chat dialog only). */
  editable?: boolean;
  className?: string;
  /** Fires once, the first time the text-session `connect` call resolves (the embed bridge). */
  onConnected?: (details: ConnectResponse) => void;
}

export function TextSessionView({
  slug,
  viaConsole = false,
  fallbackAgentName,
  editable = false,
  className,
  onConnected,
}: TextSessionViewProps) {
  const { session, details, error } = useCreateTextSession({ slug, viaConsole });

  useEffect(() => {
    if (details) onConnected?.(details);
    // `onConnected` is a per-render callback, not a dep: `details` alone decides when this fires.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [details]);

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      {/* §8: connecting is said out loud; an error says what happened and
          what to do next (`callerConnectError`), above the transcript. */}
      {error ? (
        <div className="p-3 pb-0">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : details ? null : (
        <LoadingRow label="Connecting…" className="px-3" />
      )}
      <AgentSessionProvider session={session}>
        <TextSessionInner
          session={session}
          agentName={details?.agent.name ?? fallbackAgentName}
          editable={editable}
        />
      </AgentSessionProvider>
    </div>
  );
}

function TextSessionInner({
  session,
  agentName,
  editable,
}: {
  session: ReturnType<typeof useCreateTextSession>["session"];
  agentName: string;
  editable: boolean;
}) {
  // Always called (hooks can't be conditional); only wired into `TextChat`
  // when `editable`, which is what actually shows the Edit/Replay controls.
  const actions = useTextSessionActions();
  return <TextChat session={session} agentName={agentName} actions={editable ? actions : undefined} />;
}
