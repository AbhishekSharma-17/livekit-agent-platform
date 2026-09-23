/**
 * Pure helpers turning a `useSessionMessages` transcript into 1-based user
 * "turn" numbers — the same numbering `agent/src/lkap_agent/text_mode.py`'s
 * `turn_index` uses (CONTRACTS-V2 §3.4: `rewind`/`inject_user_text`).
 *
 * A text-channel session (no STT) only ever produces `chatMessage` items for
 * the user (typed through `useChat().send`, LiveKit's `lk.chat` topic) and
 * `agentTranscript` items for the agent (no audio to transcribe from), but
 * this also handles a `userTranscript` item defensively — matching
 * `panels/blocks/transcript.tsx`'s existing role classification — in case a
 * mixed voice+text session ever renders through the same component.
 */
import type { ReceivedMessage } from "@livekit/components-react";

/** Whether `message` was said/typed by the visitor rather than the agent. */
export function isUserMessage(message: ReceivedMessage): boolean {
  if (message.type === "agentTranscript") return false;
  if (message.type === "userTranscript") return true;
  // `chatMessage` (or the type-omitted `ReceivedChatMessage` shape): local
  // participant == the visitor: the agent never has a *local* chat message
  // in this room, since it is the only remote participant.
  return message.from?.isLocal !== false;
}

/**
 * Every message's associated 1-based turn number, keyed by message id: a
 * user message gets its own turn number (for "Edit"); an agent reply gets
 * the number of the user turn it answers (for "Replay") — the count of user
 * turns at-or-before that message's position. A message before the first
 * user turn (a stored greeting, say) is left out of the map.
 *
 * De-duplicates by `id`: `useSessionMessages` can in principle carry the same
 * logical turn under more than one entry (a chat echo and a transcription of
 * the same utterance) — this is the one place that numbering would silently
 * drift from the worker's, since `text_mode.truncate_to_turn` counts each
 * `ChatContext` item exactly once.
 */
export function turnIndexByMessageId(messages: ReceivedMessage[]): Map<string, number> {
  const seen = new Set<string>();
  const byId = new Map<string, number>();
  let turn = 0;
  for (const message of messages) {
    if (seen.has(message.id)) continue;
    seen.add(message.id);
    if (isUserMessage(message)) turn += 1;
    if (turn > 0) byId.set(message.id, turn);
  }
  return byId;
}

/** The turn number associated with `messageId` (see `turnIndexByMessageId`), or `null`. */
export function turnIndexAt(messages: ReceivedMessage[], messageId: string): number | null {
  return turnIndexByMessageId(messages).get(messageId) ?? null;
}
