"use client";

/**
 * The "Live" tab (V5-38, `docs/v5/PLAN-V5.md` V5-38, `_asks.md` #251): join
 * an active session's room as a hidden, subscribe-only listener, hear it,
 * mirror its panel read-only, watch the transcript, and guide the agent with
 * a written whisper — all from the console, no separate window, never a
 * side sheet.
 *
 * `LiveSessionTab` is the registered `SessionTabDef.Component`
 * (`../detail/builtin-tabs.tsx`), visible only while `session.status ===
 * "active"`. Because that check is also enforced up in the tab registry, a
 * session ending while this tab is already open is the one case this file
 * itself has to degrade for.
 */
import * as React from "react";
import {
  CircleAlertIcon,
  ClockIcon,
  HeadphonesIcon,
  Volume2Icon,
  VolumeXIcon,
} from "lucide-react";
import { useSessionMessages } from "@livekit/components-react";

import { AgentSessionProvider } from "@/components/agents-ui/agent-session-provider";
import { AgentChatTranscript } from "@/components/agents-ui/agent-chat-transcript";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusPill, type StatusTone } from "@/components/shared/status-chip";
import { useLiveSessionEvents } from "@/components/console/lib/api-hooks";
import type { SessionDetailOut } from "@/contracts/lkap-contracts";
import { useUiState } from "@/hooks/useUiState";
import { toPanelConnectionState } from "@/lib/livekit";
import type { PanelAction } from "@/panels/registry";
import { resolvePanel } from "@/panels/registry";

import { liveTimelineEntries } from "./live-events";
import {
  EXPIRY_WARNING_MS,
  useListenSession,
  type ListenSessionPhase,
  type UseListenSessionReturn,
} from "./listen-session";
import { panelAgent } from "../session-panel-tab";
import { useSessionAgent } from "../use-session-queries";
import { WhisperBox } from "./whisper-box";

/** The mirror is read-only: nothing a supervisor does here reaches the agent. */
const noopPerform = async (_action: PanelAction): Promise<undefined> => undefined;

const PHASE_LABEL: Record<ListenSessionPhase, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  disconnected: "Disconnected",
  not_live: "Not live",
  error: "Couldn't connect",
};

const PHASE_TONE: Record<ListenSessionPhase, StatusTone> = {
  connecting: "neutral",
  live: "live",
  reconnecting: "warning",
  disconnected: "warning",
  not_live: "neutral",
  error: "danger",
};

export function LiveSessionTab({ session }: { session: SessionDetailOut }) {
  if (session.status !== "active") {
    return (
      <EmptyState
        icon={HeadphonesIcon}
        title="This call isn't live anymore"
        description="Listening in and whispering only work while a session is active."
      />
    );
  }
  // Keyed by session id: a session can only reach this tab once (a new one
  // means a fresh route), but keying defensively avoids reusing a stale
  // room/token source if that ever changes.
  return <LiveTabConnection key={session.id} session={session} />;
}

function LiveTabConnection({ session }: { session: SessionDetailOut }) {
  const listen = useListenSession(session.id);
  const [muted, setMuted] = React.useState(false);

  return (
    <AgentSessionProvider session={listen.session} muted={muted}>
      <LiveTabContent session={session} listen={listen} muted={muted} onMutedChange={setMuted} />
    </AgentSessionProvider>
  );
}

/** Exported for `tests/console-session-live.test.tsx`: rendered directly with an injected `listen`, no real `useSession` call needed. */
export function LiveTabContent({
  session,
  listen,
  muted,
  onMutedChange,
}: {
  session: SessionDetailOut;
  listen: UseListenSessionReturn;
  muted: boolean;
  onMutedChange: (muted: boolean) => void;
}) {
  const agentQuery = useSessionAgent(session.agent_id);
  const publicAgent = React.useMemo(() => panelAgent(session, agentQuery.data), [session, agentQuery.data]);
  const panel = resolvePanel(publicAgent.ui_panel_id);
  const PanelComponent = panel.Component;

  const ui = useUiState(session.id);
  const { messages } = useSessionMessages(listen.session);
  const eventsQuery = useLiveSessionEvents(session.id, true);
  const timeline = React.useMemo(() => liveTimelineEntries(eventsQuery.data?.items ?? []), [eventsQuery.data]);

  const panelConnectionState = toPanelConnectionState(listen.session.connectionState);

  return (
    <div data-slot="session-live-tab" className="flex flex-col gap-4 lg:flex-row lg:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <LiveStatusBanner
          phase={listen.phase}
          errorMessage={listen.errorMessage}
          expiresInMs={listen.expiresInMs}
          onReconnect={listen.reconnect}
        />

        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-card p-3">
          <StatusPill tone={PHASE_TONE[listen.phase]}>
            {PHASE_LABEL[listen.phase]}
          </StatusPill>
          <div className="flex items-center gap-2">
            <Icon as={muted ? VolumeXIcon : Volume2Icon} size="sm" className="text-text-secondary" />
            <Label htmlFor="live-mute-toggle" className="text-body">
              Mute
            </Label>
            <Switch
              id="live-mute-toggle"
              checked={muted}
              onCheckedChange={onMutedChange}
              aria-label={muted ? "Unmute the call audio" : "Mute the call audio"}
            />
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-border bg-card">
          {agentQuery.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <PanelComponent
              state={ui.state}
              assets={ui.assets}
              agent={publicAgent}
              sessionId={session.id}
              perform={noopPerform}
              transcript={messages}
              connectionState={panelConnectionState}
            />
          )}
        </div>

        {timeline.length > 0 ? <LiveTimeline entries={timeline} /> : null}
      </div>

      <div className="flex w-full flex-col gap-4 lg:w-80 lg:shrink-0">
        <div className="flex min-h-48 flex-col overflow-hidden rounded-lg border border-border bg-card">
          <div className="border-b border-border px-4 py-2 text-body font-medium text-foreground">Transcript</div>
          {messages.length === 0 ? (
            <p className="text-text-secondary p-4 text-body">Nothing said yet.</p>
          ) : (
            <AgentChatTranscript messages={messages} scrollAnchor="any" className="max-h-96 min-h-0 flex-1" />
          )}
        </div>

        <WhisperBox sessionId={session.id} />
      </div>
    </div>
  );
}

function LiveStatusBanner({
  phase,
  errorMessage,
  expiresInMs,
  onReconnect,
}: {
  phase: ListenSessionPhase;
  errorMessage: string | null;
  expiresInMs: number | null;
  onReconnect: () => void;
}) {
  if (phase === "disconnected") {
    return (
      <Alert variant="warning">
        <Icon as={CircleAlertIcon} size="md" />
        <AlertDescription>
          Your connection to this call dropped — most likely your listen-in link expired after 15 minutes.
        </AlertDescription>
        <AlertAction>
          <Button type="button" size="sm" variant="secondary" onClick={onReconnect}>
            Reconnect
          </Button>
        </AlertAction>
      </Alert>
    );
  }
  if (phase === "error") {
    return (
      <Alert variant="danger">
        <Icon as={CircleAlertIcon} size="md" />
        <AlertDescription>{errorMessage ?? "Couldn't connect to this call."}</AlertDescription>
        <AlertAction>
          <Button type="button" size="sm" variant="secondary" onClick={onReconnect}>
            Try again
          </Button>
        </AlertAction>
      </Alert>
    );
  }
  if (phase === "reconnecting") {
    return (
      <Alert variant="info">
        <Icon as={CircleAlertIcon} size="md" />
        <AlertDescription>Reconnecting…</AlertDescription>
      </Alert>
    );
  }
  if (phase === "live" && expiresInMs !== null && expiresInMs > 0 && expiresInMs < EXPIRY_WARNING_MS) {
    return (
      <Alert variant="info">
        <Icon as={ClockIcon} size="md" />
        <AlertDescription>Your listen-in link expires soon; you may need to reconnect shortly.</AlertDescription>
      </Alert>
    );
  }
  return null;
}

function LiveTimeline({ entries }: { entries: ReturnType<typeof liveTimelineEntries> }) {
  return (
    <div data-slot="live-timeline" className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border px-4 py-2 text-body font-medium text-foreground">Supervisor activity</div>
      <ul className="flex flex-col gap-2 p-4">
        {entries.map((entry) => (
          <li key={entry.id} className="flex items-start justify-between gap-3 text-body">
            <span className={entry.kind === "escalation" ? "text-warning-text" : "text-foreground"}>{entry.text}</span>
            <RelativeTime iso={entry.ts} className="shrink-0 text-caption text-text-secondary" />
          </li>
        ))}
      </ul>
    </div>
  );
}
