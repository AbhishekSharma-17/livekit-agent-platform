"use client";

/**
 * `transcript` block — the conversation from the room (`PanelProps.transcript`,
 * `useSessionMessages`), optionally interleaved with the agent's tool calls
 * (`show_tools`, from the envelope's `activity`). The turns never travel in
 * `UiState`; the block state only holds the option.
 */
import * as React from "react";
import { useMemo } from "react";

import type { ActivityEvent, TranscriptBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type Row =
  | { kind: "turn"; id: string; at: number; who: "agent" | "user"; text: string }
  | { kind: "tool"; id: string; at: number; event: ActivityEvent };

export function TranscriptBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<TranscriptBlockState>) {
  const showTools = data.show_tools === true;
  const rows = useMemo(() => {
    const out: Row[] = panel.transcript.map((message) => ({
      kind: "turn",
      id: message.id,
      at: message.timestamp,
      who: message.type === "agentTranscript" ? "agent" : message.type === "userTranscript" ? "user" : message.from?.isLocal === false ? "agent" : "user",
      text: message.message,
    }));
    if (showTools) {
      for (const event of panel.state.activity ?? []) {
        // Activity timestamps are epoch seconds; message timestamps are ms.
        out.push({ kind: "tool", id: `tool-${event.id}`, at: event.ts * 1000, event });
      }
    }
    return out.sort((a, b) => a.at - b.at);
  }, [panel.transcript, panel.state.activity, showTools]);

  const turns = panel.transcript.length;
  return (
    <BlockFrame spec={spec} title={title} count={turns} highlighted={highlighted}>
      {rows.length === 0 ? (
        <PanelEmpty>The conversation appears here as you talk.</PanelEmpty>
      ) : (
        <ol data-slot="block-transcript" aria-live="polite" className="max-h-96 space-y-2 overflow-y-auto pr-1">
          {rows.map((row) =>
            row.kind === "turn" ? (
              <li key={row.id} data-who={row.who} className={cn("flex", row.who === "user" && "justify-end")}>
                <p
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-1.5 text-sm leading-snug break-words",
                    row.who === "user" ? "bg-muted" : "bg-brand-soft text-foreground",
                  )}
                >
                  <span className="sr-only">{row.who === "user" ? "You: " : `${panel.agent.name}: `}</span>
                  {row.text}
                </p>
              </li>
            ) : (
              <li
                key={row.id}
                data-slot="block-transcript-tool"
                data-phase={row.event.phase}
                className="text-muted-foreground flex items-center gap-2 text-xs"
              >
                <span aria-hidden="true" className="bg-border h-px flex-1" />
                <span className="font-mono">{row.event.label}</span>
                <span>· {row.event.headline}</span>
                <span aria-hidden="true" className="bg-border h-px flex-1" />
              </li>
            ),
          )}
        </ol>
      )}
    </BlockFrame>
  );
}
