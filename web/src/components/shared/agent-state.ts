/**
 * The single agent-state vocabulary (docs/UI_UX_SPEC.md §2.1, §5.4).
 *
 * Importable from both surfaces: this file must never import from
 * `components/console/**` (the session bundle stays console-free).
 */

/** The session's state model (`StageView.agentState`, §5.4). */
export type AgentUiState =
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "reconnecting"
  | "failed"
  | "ended";

/** What the `StateMeter` can draw (§2.7). `idle` is the at-rest mark. */
export type MeterState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "failed"
  | "ended";

/** Human labels for `AgentUiState`: Connecting · Listening · Thinking · Speaking · Reconnecting · Ended. */
export const AGENT_STATE_LABEL: Record<AgentUiState, string> = {
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  reconnecting: "Reconnecting",
  failed: "Failed",
  ended: "Ended",
};

/** `aria-label` / visible label for each `StateMeter` state. */
export const STATE_LABEL: Record<MeterState, string> = {
  idle: "Idle",
  connecting: AGENT_STATE_LABEL.connecting,
  listening: AGENT_STATE_LABEL.listening,
  thinking: AGENT_STATE_LABEL.thinking,
  speaking: AGENT_STATE_LABEL.speaking,
  failed: AGENT_STATE_LABEL.failed,
  ended: AGENT_STATE_LABEL.ended,
};

/** Map a session state onto the meter (§5.4: reconnecting draws `connecting`). */
export function toMeterState(state: AgentUiState): MeterState {
  return state === "reconnecting" ? "connecting" : state;
}

export const AGENT_UI_STATES: readonly AgentUiState[] = [
  "connecting",
  "listening",
  "thinking",
  "speaking",
  "reconnecting",
  "failed",
  "ended",
];

export const METER_STATES: readonly MeterState[] = [
  "idle",
  "connecting",
  "listening",
  "thinking",
  "speaking",
  "failed",
  "ended",
];
