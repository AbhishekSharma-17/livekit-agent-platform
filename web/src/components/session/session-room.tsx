"use client";

/**
 * Everything inside the LiveKit session context: stage, transcript, live panel
 * and controls, wired to the agent ↔ UI protocol (CONTRACTS §10).
 */
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useAgent,
  useLocalParticipant,
  useSessionContext,
  useSessionMessages,
} from "@livekit/components-react";
import { Track } from "livekit-client";
import { toast } from "sonner";

import type {
  AgentPublicOut,
  UiRequest,
  UiRequestResult,
} from "@/contracts/lkap-contracts";
import { AgentChatTranscript } from "@/components/agents-ui/agent-chat-transcript";
import { AgentControlBar } from "@/components/agents-ui/agent-control-bar";
import { StartAudioButton } from "@/components/agents-ui/start-audio-button";
import { AgentStage, useLocalTrackRef } from "@/components/session/agent-stage";
import { ConnectionBanner } from "@/components/session/connection-banner";
import { SessionShell } from "@/components/session/session-shell";
import { useAgentRpc } from "@/hooks/useAgentRpc";
import { useUiRequests } from "@/hooks/useUiRequests";
import { useUiState } from "@/hooks/useUiState";
import { toPanelConnectionState } from "@/lib/livekit";
import { resolvePanel, type PanelUiAction } from "@/panels/registry";

type VideoSource = "camera" | "screen" | "none";

export interface SessionRoomProps {
  agent: AgentPublicOut;
  /** `ConnectResponse.sessionId`, or `null` until the connect call resolves. */
  sessionId: string | null;
  /** `ConnectResponse.uiPanelId`, falling back to the agent's own panel id. */
  uiPanelId: string | null;
  /** A connect-level error to surface (the token source swallows its own). */
  error?: string | null;
  onRetry: () => void;
  onEnded: () => void;
}

export function SessionRoom({
  agent,
  sessionId,
  uiPanelId,
  error,
  onRetry,
  onEnded,
}: SessionRoomProps) {
  const session = useSessionContext();
  const agentInfo = useAgent(session);
  const { messages } = useSessionMessages(session);
  const { localParticipant } = useLocalParticipant();
  const { perform, performUiAction, ready: rpcReady } = useAgentRpc();
  const ui = useUiState(sessionId);

  const [isChatOpen, setIsChatOpen] = useState(false);

  const connectionState = toPanelConnectionState(session.connectionState);
  const panel = resolvePanel(uiPanelId ?? agent.ui_panel_id);
  const capabilities = agent.capabilities;

  /* ------------------ one video source at a time (ARCH §8) -------------- */

  const cameraOn = Boolean(useLocalTrackRef(Track.Source.Camera));
  const screenOn = Boolean(useLocalTrackRef(Track.Source.ScreenShare));
  const videoSource: VideoSource = screenOn
    ? "screen"
    : cameraOn
      ? "camera"
      : "none";
  const lastSentSource = useRef<VideoSource | null>(null);

  useEffect(() => {
    if (!rpcReady) return;
    // Don't announce the initial "no video" state; only real changes.
    if (lastSentSource.current === null && videoSource === "none") {
      lastSentSource.current = "none";
      return;
    }
    if (lastSentSource.current === videoSource) return;
    lastSentSource.current = videoSource;
    void perform({
      v: 1,
      action: "set_video_source",
      payload: { source: videoSource },
    });
  }, [videoSource, rpcReady, perform]);

  /* --------------------- agent → UI requests (RPC) ---------------------- */

  const handleUiRequest = useCallback(
    async (request: UiRequest): Promise<UiRequestResult> => {
      switch (request.method) {
        case "toast": {
          const payload = request.payload ?? {};
          const message =
            typeof payload.message === "string" ? payload.message : "";
          if (!message) return { ok: false, payload: {} };
          const tone = typeof payload.tone === "string" ? payload.tone : "info";
          if (tone === "danger" || tone === "error") toast.error(message);
          else if (tone === "warning") toast.warning(message);
          else if (tone === "success") toast.success(message);
          else toast.info(message);
          return { ok: true, payload: {} };
        }
        case "request_video_source": {
          const requested = request.payload?.source;
          if (requested === "camera" && capabilities.camera) {
            await localParticipant.setScreenShareEnabled(false);
            await localParticipant.setCameraEnabled(true);
            return { ok: true, payload: { source: "camera" } };
          }
          if (requested === "screen" && capabilities.screen_share) {
            await localParticipant.setCameraEnabled(false);
            await localParticipant.setScreenShareEnabled(true);
            return { ok: true, payload: { source: "screen" } };
          }
          if (requested === "none") {
            await localParticipant.setCameraEnabled(false);
            await localParticipant.setScreenShareEnabled(false);
            return { ok: true, payload: { source: "none" } };
          }
          return {
            ok: false,
            payload: { error: `video source "${String(requested)}" unavailable` },
          };
        }
        default:
          // `open_dialog` / `focus` target panel-specific affordances; the
          // generic panel has none, so they are politely declined.
          return {
            ok: false,
            payload: { error: `${request.method} is not supported here` },
          };
      }
    },
    [capabilities.camera, capabilities.screen_share, localParticipant],
  );

  useUiRequests(handleUiRequest);

  /* ------------------------------- panel -------------------------------- */

  const panelPerform = useCallback(
    (action: PanelUiAction) =>
      performUiAction(action.payload.name, action.payload.data),
    [performUiAction],
  );

  const PanelComponent = panel.Component;
  const panelNode = useMemo(
    () => (
      <PanelComponent
        state={ui.state}
        assets={ui.assets}
        agent={agent}
        sessionId={sessionId ?? ""}
        perform={panelPerform}
        transcript={messages}
        connectionState={connectionState}
      />
    ),
    [
      PanelComponent,
      ui.state,
      ui.assets,
      agent,
      sessionId,
      panelPerform,
      messages,
      connectionState,
    ],
  );

  // Single teardown path: unmounting `LiveSession` freezes the token source and
  // ends the room, so this must not call `session.end()` itself.
  const handleDisconnect = useCallback(() => {
    onEnded();
  }, [onEnded]);

  return (
    <SessionShell
      layout={panel.layout ?? "side"}
      panelTitle={panel.title}
      banner={
        <ConnectionBanner
          connectionState={connectionState}
          agentState={agentInfo.state}
          failureReasons={agentInfo.failureReasons}
          error={error ?? null}
          onRetry={onRetry}
        />
      }
      stage={<AgentStage agentName={agent.name} compact={panel.layout === "wide"} />}
      transcript={
        <>
          <header className="border-border/60 flex shrink-0 items-center justify-between border-b px-4 py-2.5">
            <h2 className="text-sm font-semibold tracking-tight">Transcript</h2>
            <StartAudioButton
              size="sm"
              variant="outline"
              label="Enable sound"
            />
          </header>
          <AgentChatTranscript
            agentState={agentInfo.state}
            messages={messages}
            scrollAnchor="any"
            className="min-h-0 flex-1"
          />
        </>
      }
      panel={panelNode}
      controls={
        <AgentControlBar
          variant="livekit"
          isConnected={session.isConnected}
          isChatOpen={isChatOpen}
          onIsChatOpenChange={setIsChatOpen}
          onDisconnect={handleDisconnect}
          onDeviceError={({ source, error: deviceError }) =>
            toast.error(`Could not start ${String(source)}: ${deviceError.message}`)
          }
          controls={{
            leave: true,
            microphone: true,
            camera: capabilities.camera ?? false,
            screenShare: capabilities.screen_share ?? false,
            chat: capabilities.chat_input ?? false,
          }}
        />
      }
    />
  );
}
