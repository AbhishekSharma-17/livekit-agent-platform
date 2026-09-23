/**
 * `components/session/embed/turns.ts` (V2-18): turn numbering must match
 * `agent/src/lkap_agent/text_mode.py::truncate_to_turn`'s (1-based, counts
 * only user messages) exactly — this is the number sent back to the worker
 * over `rewind`/`inject_user_text`.
 */
import { describe, expect, it } from "vitest";
import type { ReceivedMessage } from "@livekit/components-react";

import { isUserMessage, turnIndexAt, turnIndexByMessageId } from "@/components/session/embed/turns";

function chat(id: string, message: string, isLocal: boolean): ReceivedMessage {
  return {
    id,
    timestamp: 0,
    type: "chatMessage",
    message,
    from: { isLocal } as ReceivedMessage["from"],
  } as ReceivedMessage;
}

function agentTranscript(id: string, message: string): ReceivedMessage {
  return { id, timestamp: 0, type: "agentTranscript", message } as ReceivedMessage;
}

function userTranscript(id: string, message: string): ReceivedMessage {
  return { id, timestamp: 0, type: "userTranscript", message } as ReceivedMessage;
}

describe("isUserMessage", () => {
  it("a local chatMessage is the user's", () => {
    expect(isUserMessage(chat("1", "hi", true))).toBe(true);
  });

  it("a non-local chatMessage is the agent's", () => {
    expect(isUserMessage(chat("1", "hi", false))).toBe(false);
  });

  it("agentTranscript is always the agent's", () => {
    expect(isUserMessage(agentTranscript("1", "hi"))).toBe(false);
  });

  it("userTranscript is always the user's", () => {
    expect(isUserMessage(userTranscript("1", "hi"))).toBe(true);
  });
});

describe("turnIndexByMessageId", () => {
  it("numbers user turns 1-based and maps each agent reply to the turn it answers", () => {
    const messages = [
      agentTranscript("greet", "Hi there!"),
      chat("u1", "hello", true),
      agentTranscript("a1", "hi!"),
      chat("u2", "what's 2+2", true),
      agentTranscript("a2", "4"),
    ];

    const byId = turnIndexByMessageId(messages);

    expect(byId.get("greet")).toBeUndefined(); // before any user turn
    expect(byId.get("u1")).toBe(1);
    expect(byId.get("a1")).toBe(1);
    expect(byId.get("u2")).toBe(2);
    expect(byId.get("a2")).toBe(2);
  });

  it("de-duplicates a repeated id (e.g. a chat echo re-delivered)", () => {
    const messages = [chat("u1", "hello", true), chat("u1", "hello", true), chat("u2", "again", true)];

    expect(turnIndexByMessageId(messages).get("u2")).toBe(2);
  });

  it("an empty transcript maps nothing", () => {
    expect(turnIndexByMessageId([]).size).toBe(0);
  });
});

describe("turnIndexAt", () => {
  it("returns null for an id not in the transcript", () => {
    expect(turnIndexAt([chat("u1", "hi", true)], "missing")).toBeNull();
  });

  it("matches turnIndexByMessageId for a known id", () => {
    const messages = [chat("u1", "hi", true), agentTranscript("a1", "hello")];
    expect(turnIndexAt(messages, "a1")).toBe(1);
  });
});
