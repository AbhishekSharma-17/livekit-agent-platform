"use client";

/**
 * `transcript` block — the conversation from the room (`PanelProps.transcript`,
 * `useSessionMessages`), optionally interleaved with the agent's tool calls
 * (`show_tools`, from the envelope's `activity`). The turns never travel in
 * `UiState`; the block state only holds the option.
 *
 * **Language chip (V5-35).** A stored `TranscriptTurn.language` exists only
 * for a *finished* session (`session-transcript.tsx`, outside this card's
 * files); this live block has no such field on `panel.transcript` — the
 * turns come from `useSessionMessages`, unchanged by V5-35's contracts
 * commit. So a live turn's language is read off the matching
 * `lkap.captions` segment instead (`composite/captions-stream.ts`, ask
 * #203(2): "live turns can take the language from the caption segments"),
 * matched by speaker and exact text. There is no per-session "default
 * language" available here either (`PanelProps.agent` carries no voice
 * config) — the chip fires when a turn's language differs from the first
 * language any caption segment reported in the session, a caller-observed
 * baseline rather than the agent's configured default. A stored session's
 * own transcript view has the real thing and needs no such heuristic; an
 * ask is filed to give it the same chip from `TranscriptTurn.language`.
 */
import * as React from "react";
import { useMemo } from "react";

import { StatusChip } from "@/components/shared/status-chip";
import type { ActivityEvent, TranscriptBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { languageLabel } from "@/panels/blocks/catalog";
import { useCaptionsStream } from "@/panels/composite/captions-stream";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type Row =
  | { kind: "turn"; id: string; at: number; who: "agent" | "user"; text: string; language: string | null }
  | { kind: "tool"; id: string; at: number; event: ActivityEvent };

function TranscriptBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<TranscriptBlockState>) {
  const showTools = data.show_tools === true;
  const captionSegments = useCaptionsStream();

  const { languageByTurn, baselineLanguage } = useMemo(() => {
    const byTurn = new Map<string, string>();
    let baseline: string | null = null;
    for (const segment of captionSegments) {
      if (!segment.final || !segment.language) continue;
      byTurn.set(`${segment.speaker}:${segment.text}`, segment.language);
      if (baseline === null) baseline = segment.language;
    }
    return { languageByTurn: byTurn, baselineLanguage: baseline };
  }, [captionSegments]);

  const rows = useMemo(() => {
    const out: Row[] = panel.transcript.map((message) => {
      const speaker: "agent" | "user" =
        message.type === "agentTranscript" ? "agent" : message.type === "userTranscript" ? "user" : message.from?.isLocal === false ? "agent" : "user";
      return {
        kind: "turn",
        id: message.id,
        at: message.timestamp,
        who: speaker,
        text: message.message,
        language: languageByTurn.get(`${speaker}:${message.message}`) ?? null,
      };
    });
    if (showTools) {
      for (const event of panel.state.activity ?? []) {
        // Activity timestamps are epoch seconds; message timestamps are ms.
        out.push({ kind: "tool", id: `tool-${event.id}`, at: event.ts * 1000, event });
      }
    }
    return out.sort((a, b) => a.at - b.at);
  }, [panel.transcript, panel.state.activity, showTools, languageByTurn]);

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
                  {row.language && row.language !== baselineLanguage ? (
                    <StatusChip tone="neutral" size="sm" className="mr-1.5 align-middle">
                      {languageLabel(row.language)}
                    </StatusChip>
                  ) : null}
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

export { TranscriptBlock };
export default TranscriptBlock;
