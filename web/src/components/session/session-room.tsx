"use client";

/**
 * Everything inside the LiveKit session context: stage, transcript, live panel
 * and controls, wired to the agent ↔ UI protocol (CONTRACTS §10) and to the
 * §5.3/§5.4 shell.
 *
 * This is the only file that turns room state into the session's state model:
 * `toAgentUiState()` (pure, `session-state.ts`) feeds `StageView` through
 * `AgentStage`, and the same value decides the top strip, the banner and
 * which controls are live.
 *
 * Audio playback (§5.2): the visitor's Start click already created and
 * resumed an `AudioContext` which `LiveSession` handed to the room
 * (`webAudioMix`), so playback normally just works. As a belt-and-braces
 * fallback this calls `room.startAudio()` once connected and, if the browser
 * still refuses, the stage shows the "Tap to hear <agent>" overlay.
 */
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useAgent,
  useLocalParticipant,
  useSessionContext,
  useSessionMessages,
  useStartAudio,
} from "@livekit/components-react";
import { Track } from "livekit-client";
import { toast } from "sonner";

import type {
  AgentPublicOut,
  UiRequest,
  UiRequestResult,
} from "@/contracts/lkap-contracts";
import { StatusChip } from "@/components/shared/status-chip";
import { AgentChatTranscript } from "@/components/agents-ui/agent-chat-transcript";
import { AgentStage, useLocalTrackRef } from "@/components/session/agent-stage";
import { ConnectionBanner } from "@/components/session/connection-banner";
import { SessionControls } from "@/components/session/session-controls";
import { SessionShell } from "@/components/session/session-shell";
import { TestModeBar } from "@/components/session/test-mode-bar";
import {
  screenShareSupported,
  toAgentUiState,
} from "@/components/session/session-state";
import { useAgentRpc } from "@/hooks/useAgentRpc";
import { useUiRequests } from "@/hooks/useUiRequests";
import { useUiState } from "@/hooks/useUiState";
import { toPanelConnectionState } from "@/lib/livekit";
import { resolvePanel, type PanelDefinition, type PanelUiAction } from "@/panels/registry";

type VideoSource = "camera" | "screen" | "none";
type DeviceKey = "microphone" | "camera" | "screenShare";

const DEVICE_KEY: Record<string, DeviceKey> = {
  [Track.Source.Microphone]: "microphone",
  [Track.Source.Camera]: "camera",
  [Track.Source.ScreenShare]: "screenShare",
};

const DEVICE_MESSAGE: Record<DeviceKey, string> = {
  microphone: "Couldn't start your microphone — check permissions",
  camera: "Couldn't start your camera — check permissions",
  screenShare: "Couldn't share your screen",
};

export interface SessionRoomProps {
  agent: AgentPublicOut;
  /** `ConnectResponse.sessionId`, or `null` until the connect call resolves. */
  sessionId: string | null;
  /** `ConnectResponse.uiPanelId`, falling back to the agent's own panel id. */
  uiPanelId: string | null;
  /** A connect-level error to surface (the token source swallows its own). */
  error?: string | null;
  /** `?mode=test`: show the test bar and the way back to the editor. */
  testMode?: boolean;
  onRetry: () => void;
  /** Leave without ending the call normally (the failure overlay). The
   * reason travels back so the pre-call card can show it above Start. */
  onLeave: (reason?: string | null) => void;
  onEnded: (durationMs: number) => void;
}

export function SessionRoom({
  agent,
  sessionId,
  uiPanelId,
  error,
  testMode,
  onRetry,
  onLeave,
  onEnded,
}: SessionRoomProps) {
  const session = useSessionContext();
  const agentInfo = useAgent(session);
  const { messages } = useSessionMessages(session);
  const { localParticipant } = useLocalParticipant();
  const { perform, performUiAction, ready: rpcReady } = useAgentRpc();
  const ui = useUiState(sessionId);
  const { mergedProps: startAudioProps, canPlayAudio } = useStartAudio({
    room: session.room,
    props: {},
  });

  const [isChatOpen, setIsChatOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [deviceErrors, setDeviceErrors] = useState<
    Partial<Record<DeviceKey, boolean>>
  >({});
  const [elapsedMs, setElapsedMs] = useState<number | undefined>(undefined);

  const connectionState = toPanelConnectionState(session.connectionState);
  const panel = resolvePanel(uiPanelId ?? agent.ui_panel_id);
  const capabilities = agent.capabilities;

  /* ------------------------- §5.4 state model --------------------------- */

  const hasConnectedRef = useRef(false);
  if (connectionState === "connected") hasConnectedRef.current = true;
  const agentState = toAgentUiState({
    connectionState,
    agentState: agentInfo.state,
    hasConnected: hasConnectedRef.current,
    error,
  });

  /* ------------------------------ timer --------------------------------- */

  const startedAtRef = useRef<number | null>(null);
  if (connectionState === "connected" && startedAtRef.current === null) {
    startedAtRef.current = Date.now();
  }
  useEffect(() => {
    if (startedAtRef.current === null) return;
    const tick = () =>
      setElapsedMs(Date.now() - (startedAtRef.current ?? Date.now()));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [connectionState]);

  /* ------------------------ end of call (§5.6) --------------------------- */

  const endedRef = useRef(false);
  useEffect(() => {
    if (agentState !== "ended" || endedRef.current) return;
    endedRef.current = true;
    onEnded(
      startedAtRef.current === null ? 0 : Date.now() - startedAtRef.current,
    );
  }, [agentState, onEnded]);

  /* --------------------- audio playback (§5.2) --------------------------- */

  const startedAudioRef = useRef(false);
  useEffect(() => {
    if (connectionState !== "connected" || startedAudioRef.current) return;
    startedAudioRef.current = true;
    void session.room.startAudio().catch(() => {
      // The overlay below takes over; `canPlayAudio` stays false.
    });
  }, [connectionState, session.room]);

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

  // A successful toggle clears that control's danger dot (§5.4).
  useEffect(() => {
    if (cameraOn) setDeviceErrors((prev) => ({ ...prev, camera: false }));
  }, [cameraOn]);
  useEffect(() => {
    if (screenOn) setDeviceErrors((prev) => ({ ...prev, screenShare: false }));
  }, [screenOn]);

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
        default: {
          // `open_dialog` / `focus` target panel-specific affordances (§5.4).
          // A panel opts in with `PanelDefinition.handleRequest`; until one
          // does, the room keeps declining politely.
          const handleRequest = (
            panel as PanelDefinition & {
              handleRequest?: (
                request: UiRequest,
              ) => UiRequestResult | Promise<UiRequestResult>;
            }
          ).handleRequest;
          if (typeof handleRequest === "function") {
            return await handleRequest(request);
          }
          return {
            ok: false,
            payload: { error: `${request.method} is not supported here` },
          };
        }
      }
    },
    [capabilities.camera, capabilities.screen_share, localParticipant, panel],
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
    onEnded(
      startedAtRef.current === null ? 0 : Date.now() - startedAtRef.current,
    );
    endedRef.current = true;
  }, [onEnded]);

  const handleDeviceError = useCallback(
    ({ source, error: deviceError }: { source: Track.Source; error: Error }) => {
      const key = DEVICE_KEY[source] ?? "microphone";
      setDeviceErrors((prev) => ({ ...prev, [key]: true }));
      toast.error(DEVICE_MESSAGE[key], { description: deviceError.message });
    },
    [],
  );

  const layout = panel.layout ?? "side";
  const status = ui.state.status;

  return (
    <SessionShell
      layout={layout}
      panelTitle={panel.title}
      panelStatus={
        layout === "wide" && status ? (
          <StatusChip tone={status.tone ?? "neutral"} dot>
            {status.label}
          </StatusChip>
        ) : undefined
      }
      agentName={agent.name}
      agentState={agentState}
      elapsedMs={elapsedMs}
      testBar={
        testMode ? <TestModeBar backHref={`/console/agents/${agent.id}`} /> : undefined
      }
      banner={<ConnectionBanner agentState={agentState} />}
      stage={
        <AgentStage
          agentName={agent.name}
          agentState={agentState}
          compact={layout === "wide"}
          elapsedMs={elapsedMs}
          audioBlocked={connectionState === "connected" && !canPlayAudio}
          onEnableAudio={startAudioProps.onClick}
          failureReasons={agentInfo.failureReasons ?? (error ? [error] : null)}
          onRetry={onRetry}
          onLeave={() =>
            onLeave(
              error ??
                (agentInfo.failureReasons?.length
                  ? agentInfo.failureReasons.join("; ")
                  : null),
            )
          }
        />
      }
      transcript={
        messages.length === 0 ? (
          <p className="text-muted-foreground p-4 text-sm">
            Say hello — the transcript appears here
          </p>
        ) : (
          <AgentChatTranscript
            agentState={agentInfo.state}
            messages={messages}
            scrollAnchor="any"
            className="min-h-0 flex-1"
          />
        )
      }
      transcriptCount={messages.length}
      transcriptOpen={transcriptOpen || isChatOpen}
      onTranscriptOpenChange={setTranscriptOpen}
      panel={panelNode}
      controls={
        <SessionControls
          agentState={agentState}
          capabilities={capabilities}
          screenShareSupported={screenShareSupported()}
          isConnected={session.isConnected}
          isChatOpen={isChatOpen}
          onIsChatOpenChange={(open) => {
            setIsChatOpen(open);
            if (open) setTranscriptOpen(true);
          }}
          onDisconnect={handleDisconnect}
          onDeviceError={handleDeviceError}
          deviceErrors={deviceErrors}
        />
      }
    />
  );
}
