"use client";

/**
 * `activity` block — the envelope's `activity`, newest first (WP-9
 * `ActivityBlock`), plus one live "working on" line (V5-44, `docs/v5/PLAN-V5.md`
 * V5-44's E2, `docs/v5/_asks.md` #310's sibling scope): the running tool's
 * own headline when one is in flight, else the room's live `agent_state`
 * (`listening` / `thinking` / `speaking`) in plain words.
 *
 * `agent_state` is a LiveKit participant attribute (`lk.agent.state`, set by
 * the agents SDK on the agent participant — the same value the worker's
 * `agent_state` session event carries), not part of `UiState`, so nothing in
 * `PanelProps` carries it; this reads it straight off the room the same way
 * `composite/captions-stream.ts` reads `lkap.captions` instead of a
 * components-react hook. That needs `@livekit/components-react`, so — like
 * `video`/`upload`/`captions`/`transcript` before it — this block joins
 * `LAZY_BLOCK_TYPES` (`./index.tsx`) rather than pull that into every
 * session's first load. Outside a room (the console preview, a read-only
 * session snapshot, a test with no `RoomContext`) this renders the tool
 * headline only, exactly as `video`'s empty state does with no camera.
 */
import * as React from "react";
import { useMaybeRoomContext } from "@livekit/components-react";
import { ParticipantKind, RoomEvent, type Room } from "livekit-client";

import type { ActivityEvent } from "@/contracts/lkap-contracts";
import { ActivityBlock as ActivityView, sortActivity } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

/** `@livekit/components-core`'s `ParticipantAgentAttributes.AgentState` — not imported
 * directly (a transitive dependency the web `package.json` does not declare). */
const AGENT_STATE_ATTRIBUTE = "lk.agent.state";
/** ...`ParticipantAgentAttributes.PublishOnBehalf` — excludes the avatar worker's own
 * participant, which mirrors the agent's attributes rather than owning its state. */
const PUBLISH_ON_BEHALF_ATTRIBUTE = "lk.publish_on_behalf";

/** The agent SDK's own pipeline states (`useAgent.ts`'s `AgentSdkStates`); only these three mean "doing something". */
const AGENT_STATE_LINE: Record<string, string> = {
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

function agentStateOf(room: Room): string | null {
  for (const participant of room.remoteParticipants.values()) {
    if (participant.kind === ParticipantKind.AGENT && !(PUBLISH_ON_BEHALF_ATTRIBUTE in participant.attributes)) {
      return participant.attributes[AGENT_STATE_ATTRIBUTE] ?? null;
    }
  }
  return null;
}

/** The agent's live pipeline state off the room's participant attributes, `null` outside a room. */
function useRoomAgentState(): string | null {
  const room = useMaybeRoomContext();
  const [state, setState] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!room) {
      setState(null);
      return undefined;
    }
    setState(agentStateOf(room));
    const refresh = () => setState(agentStateOf(room));
    room.on(RoomEvent.ParticipantAttributesChanged, refresh);
    room.on(RoomEvent.ParticipantConnected, refresh);
    room.on(RoomEvent.ParticipantDisconnected, refresh);
    return () => {
      room.off(RoomEvent.ParticipantAttributesChanged, refresh);
      room.off(RoomEvent.ParticipantConnected, refresh);
      room.off(RoomEvent.ParticipantDisconnected, refresh);
    };
  }, [room]);

  return state;
}

/**
 * The one line to show: the newest running tool's headline wins (it is more
 * specific than "Thinking"); otherwise the room's own state in plain words;
 * otherwise nothing (idle, connecting, or no room at all).
 */
export function workingOnLine(agentState: string | null, events: ActivityEvent[]): string | null {
  const running = sortActivity(events).find((event) => event.phase === "running");
  if (running) return `Working on: ${running.headline}`;
  return agentState ? (AGENT_STATE_LINE[agentState] ?? null) : null;
}

export function ActivityBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const events = panel.state.activity ?? [];
  const agentState = useRoomAgentState();
  const line = workingOnLine(agentState, events);
  return (
    <BlockFrame spec={spec} title={title} count={events.length} highlighted={highlighted}>
      {line && (
        <p role="status" aria-live="polite" className="text-muted-foreground mb-2.5 text-[0.8125rem]">
          {line}
        </p>
      )}
      <ActivityView events={events} />
    </BlockFrame>
  );
}

export default ActivityBlock;
