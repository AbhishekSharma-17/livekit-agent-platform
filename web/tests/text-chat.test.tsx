/**
 * `components/session/embed/text-chat.tsx` (V2-18): renders a fixture
 * transcript and exercises the edit/replay affordances that only appear when
 * `actions` is supplied (the console drawer; the embed layout omits it,
 * UI_UX_SPEC-V2-AMENDMENTS §2.6).
 */
import * as React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReceivedMessage, UseSessionReturn } from "@livekit/components-react";

const send = vi.fn().mockResolvedValue(undefined);
const useSessionMessagesMock = vi.fn();

vi.mock("@livekit/components-react", async () => {
  const actual = await vi.importActual<object>("@livekit/components-react");
  return {
    ...actual,
    useSessionMessages: (...args: unknown[]) => useSessionMessagesMock(...args),
    useChat: () => ({ send, isSending: false }),
  };
});

import { TextChat } from "@/components/session/embed/text-chat";

const FIXTURE: ReceivedMessage[] = [
  { id: "u1", timestamp: 0, type: "chatMessage", message: "hi", from: { isLocal: true } } as ReceivedMessage,
  { id: "a1", timestamp: 1, type: "agentTranscript", message: "hello!" } as ReceivedMessage,
];

const SESSION = {} as UseSessionReturn;

function setMessages(messages: ReceivedMessage[]) {
  useSessionMessagesMock.mockReturnValue({ messages, isSending: false, send, internal: { emitter: {} } });
}

describe("TextChat", () => {
  beforeEach(() => {
    send.mockClear();
    setMessages(FIXTURE);
  });

  it("renders the fixture transcript as user/agent bubbles", () => {
    render(<TextChat session={SESSION} agentName="Ada" />);
    expect(screen.getByText("hi")).toBeTruthy();
    expect(screen.getByText("hello!")).toBeTruthy();
    expect(screen.getByText("hi").closest("li")?.getAttribute("data-who")).toBe("user");
    expect(screen.getByText("hello!").closest("li")?.getAttribute("data-who")).toBe("agent");
  });

  it("shows an empty-state prompt with no messages yet", () => {
    setMessages([]);
    render(<TextChat session={SESSION} agentName="Ada" />);
    expect(screen.getByText(/Say hello to start/)).toBeTruthy();
  });

  it("without `actions`, shows no Edit/Replay controls (the embed layout's plain composer)", () => {
    render(<TextChat session={SESSION} agentName="Ada" />);
    expect(screen.queryByText("Edit")).toBeNull();
    expect(screen.queryByText("Replay")).toBeNull();
  });

  it("with `actions`, Edit appears on the user turn and Replay on the agent's reply", () => {
    const actions = { rewind: vi.fn().mockResolvedValue({ ok: true }), editTurn: vi.fn().mockResolvedValue({ ok: true }) };
    render(<TextChat session={SESSION} agentName="Ada" actions={actions} />);
    expect(screen.getByText("Edit")).toBeTruthy();
    expect(screen.getByText("Replay")).toBeTruthy();
  });

  it("Replay calls actions.rewind with the turn the reply answers", async () => {
    const actions = { rewind: vi.fn().mockResolvedValue({ ok: true }), editTurn: vi.fn().mockResolvedValue({ ok: true }) };
    render(<TextChat session={SESSION} agentName="Ada" actions={actions} />);
    fireEvent.click(screen.getByText("Replay"));
    await waitFor(() => expect(actions.rewind).toHaveBeenCalledWith(1));
  });

  it("Edit swaps the bubble for an inline form; Save calls actions.editTurn with turnIndex-1", async () => {
    const actions = { rewind: vi.fn().mockResolvedValue({ ok: true }), editTurn: vi.fn().mockResolvedValue({ ok: true }) };
    render(<TextChat session={SESSION} agentName="Ada" actions={actions} />);
    fireEvent.click(screen.getByText("Edit"));
    const input = screen.getByLabelText(/Edit your message/);
    fireEvent.change(input, { target: { value: "hi there" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(actions.editTurn).toHaveBeenCalledWith(1, "hi there"));
  });

  it("the composer sends typed text via useChat().send", () => {
    render(<TextChat session={SESSION} agentName="Ada" />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "another question" } });
    fireEvent.click(screen.getByText("Send"));
    expect(send).toHaveBeenCalledWith("another question");
  });
});
