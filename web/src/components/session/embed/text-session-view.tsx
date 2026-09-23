"use client";

/**
 * A complete text-channel session: connects, then renders `TextChat` inside
 * the provider it needs (see `use-text-session.ts`'s module docstring for why
 * session-creation and `useTextSessionActions`/`TextChat` are split into two
 * components). Shared by the widget's embed text mode (`editable={false}`,
 * UI_UX_SPEC-V2-AMENDMENTS §2.6) and the console's **Test chat** drawer
 * (`editable`, per-turn Edit/Replay — `components/console/agents/test-chat/`).
 */
import { useEffect } from "react";
import { AgentSessionProvider } from "@/components/agents-ui/agent-session-provider";
import { cn } from "@/lib/utils";

import type { ConnectResponse } from "@/contracts/lkap-contracts";

import { TextChat } from "./text-chat";
import { useCreateTextSession, useTextSessionActions } from "./use-text-session";

export interface TextSessionViewProps {
  slug: string;
  /** `?mode=test` / the console drawer: route through the admin proxy (DECISIONS-W2 D-W2-1). */
  viaConsole?: boolean;
  /** Shown until the session's own `ConnectResponse.agent.name` arrives. */
  fallbackAgentName: string;
  /** Per-turn Edit/Replay controls (console drawer only). */
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
      {error ? (
        <p role="alert" className="text-danger-text p-3 text-sm">
          {error}
        </p>
      ) : null}
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
