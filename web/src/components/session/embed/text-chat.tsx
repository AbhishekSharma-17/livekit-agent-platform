"use client";

/**
 * Transcript-first text session UI (UI_UX_SPEC-V2-AMENDMENTS §2.6: "Text
 * mode: transcript-first layout, composer pinned, no mic controls"). Reused
 * by both the console **Test chat** dialog (`actions` supplied → per-turn
 * Edit/Replay) and the widget's embed text mode (`actions` omitted → plain
 * transcript + composer, exactly §2.6's row).
 *
 * Must be rendered *inside* `<AgentSessionProvider session={session}>` — see
 * `use-text-session.ts`'s module docstring for why the two are split.
 * Bubble styling mirrors `panels/blocks/transcript.tsx` (the generic panel's
 * transcript block) so the two don't diverge.
 */
import * as React from "react";
import { useMemo, useState } from "react";
import { useChat, useSessionMessages, type UseSessionReturn } from "@livekit/components-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { isUserMessage, turnIndexByMessageId } from "./turns";
import type { UseTextSessionActions } from "./use-text-session";

export interface TextChatProps {
  session: UseSessionReturn;
  agentName: string;
  /** Present only in the console Test chat dialog: enables per-turn Edit/Replay. */
  actions?: UseTextSessionActions;
  className?: string;
}

export function TextChat({ session, agentName, actions, className }: TextChatProps) {
  const { messages } = useSessionMessages(session);
  const { send, isSending } = useChat();
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [busyTurn, setBusyTurn] = useState<number | null>(null);

  const turnById = useMemo(() => turnIndexByMessageId(messages), [messages]);

  async function submitComposer(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || isSending) return;
    setDraft("");
    await send(text);
  }

  async function replay(turnIndex: number) {
    if (!actions || busyTurn !== null) return;
    setBusyTurn(turnIndex);
    try {
      await actions.rewind(turnIndex);
    } finally {
      setBusyTurn(null);
    }
  }

  function startEdit(messageId: string, currentText: string) {
    setEditingId(messageId);
    setEditValue(currentText);
  }

  async function submitEdit(turnIndex: number) {
    if (!actions || busyTurn !== null) return;
    const text = editValue.trim();
    if (!text) return;
    setBusyTurn(turnIndex);
    try {
      await actions.editTurn(turnIndex, text);
    } finally {
      setBusyTurn(null);
      setEditingId(null);
    }
  }

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)} data-slot="text-chat">
      <ol aria-live="polite" className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <li className="text-muted-foreground py-8 text-center text-sm">
            Say hello to start the conversation.
          </li>
        ) : null}
        {messages.map((message) => {
          const isUser = isUserMessage(message);
          const turnIndex = turnById.get(message.id) ?? null;
          const isEditing = editingId === message.id;
          return (
            <li
              key={message.id}
              data-who={isUser ? "user" : "agent"}
              data-turn-index={turnIndex ?? undefined}
              className={cn("flex flex-col gap-1", isUser && "items-end")}
            >
              {isEditing ? (
                <form
                  className="flex w-full max-w-[85%] gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (turnIndex !== null) void submitEdit(turnIndex);
                  }}
                >
                  <Input
                    autoFocus
                    value={editValue}
                    onChange={(event) => setEditValue(event.target.value)}
                    aria-label={`Edit your message (turn ${turnIndex ?? ""})`}
                  />
                  <Button type="submit" size="sm" disabled={busyTurn !== null}>
                    Save
                  </Button>
                  <Button type="button" size="sm" variant="outline" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </form>
              ) : (
                <p
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-1.5 text-sm leading-snug break-words",
                    isUser ? "bg-muted" : "bg-brand-soft text-foreground",
                  )}
                >
                  <span className="sr-only">{isUser ? "You: " : `${agentName}: `}</span>
                  {message.message}
                </p>
              )}
              {actions && !isEditing ? (
                <div className="flex gap-2 text-xs">
                  {isUser && turnIndex !== null ? (
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground underline underline-offset-2 disabled:opacity-50"
                      onClick={() => startEdit(message.id, message.message)}
                      disabled={busyTurn !== null}
                    >
                      Edit
                    </button>
                  ) : null}
                  {!isUser && turnIndex !== null ? (
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground underline underline-offset-2 disabled:opacity-50"
                      onClick={() => void replay(turnIndex)}
                      disabled={busyTurn !== null}
                    >
                      {busyTurn === turnIndex ? "Replaying…" : "Replay"}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      <form
        onSubmit={(event) => void submitComposer(event)}
        className="border-border flex items-center gap-2 border-t p-3"
        data-slot="text-chat-composer"
      >
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Type a message…"
          aria-label="Message"
          disabled={isSending}
        />
        <Button type="submit" disabled={isSending || draft.trim() === ""}>
          Send
        </Button>
      </form>
    </div>
  );
}
