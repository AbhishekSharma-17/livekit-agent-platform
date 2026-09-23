"use client";

/**
 * Pure layout for the live session surface (docs/UI_UX_SPEC.md §5.3).
 *
 * Free of LiveKit so the layout seam is testable without a room: the class
 * strings come from `session-layout.ts`, everything else is `ReactNode`.
 *
 * - `"side"` (generic panel): stage over transcript on the left, panel right.
 * - `"wide"` (insurance notebook): a 340 px rail (stage over transcript) with
 *   the panel filling the main column.
 * - Below `lg` both collapse to one scrolling column with a `fixed`,
 *   safe-area-aware control bar and the transcript as a bottom sheet.
 */
import * as React from "react";
import { ChevronDownIcon, MessageSquareTextIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import {
  AGENT_STATE_LABEL,
  type AgentUiState,
} from "@/components/shared/agent-state";
import {
  sessionLayoutModel,
  type SessionLayout,
} from "@/components/session/session-layout";
import { formatElapsed } from "@/components/session/session-state";
import { cn } from "@/lib/utils";

export interface SessionShellProps {
  layout: SessionLayout;
  /** Title shown above the panel column. */
  panelTitle: string;
  /** Optional chip beside the panel title (the notebook's claim status). */
  panelStatus?: React.ReactNode;
  agentName: string;
  agentState: AgentUiState;
  /** Elapsed call time; the timer only appears once connected. */
  elapsedMs?: number;
  /** The slim test-mode bar, above everything else (§5.1). */
  testBar?: React.ReactNode;
  /** The reconnecting banner (§5.4). */
  banner?: React.ReactNode;
  stage: React.ReactNode;
  transcript: React.ReactNode;
  transcriptCount?: number;
  /** Bottom-sheet state below `lg`; ignored from `lg` up (always visible). */
  transcriptOpen?: boolean;
  onTranscriptOpenChange?: (open: boolean) => void;
  panel: React.ReactNode;
  controls: React.ReactNode;
}

const LIVE_STATES: AgentUiState[] = ["listening", "thinking", "speaking"];

export function SessionShell({
  layout,
  panelTitle,
  panelStatus,
  agentName,
  agentState,
  elapsedMs,
  testBar,
  banner,
  stage,
  transcript,
  transcriptCount = 0,
  transcriptOpen = false,
  onTranscriptOpenChange,
  panel,
  controls,
}: SessionShellProps) {
  const model = sessionLayoutModel(layout);
  const live = LIVE_STATES.includes(agentState);

  return (
    <div
      data-testid="session-shell"
      data-layout={layout}
      data-state={agentState}
      className={model.root}
    >
      {testBar}

      {/* Top strip (§5.3): who, how long, and the connection state. */}
      <header
        data-testid="session-top-strip"
        className="flex h-10 shrink-0 items-center gap-2 px-3 lg:px-4"
      >
        <span className="text-foreground min-w-0 truncate text-sm font-medium">
          {agentName}
        </span>
        {live && <StatusChip tone="live">Live</StatusChip>}
        <span className="flex-1" />
        {elapsedMs !== undefined && live && (
          <span
            data-testid="top-strip-elapsed"
            className="text-muted-foreground font-mono text-[0.8125rem] tabular-nums"
          >
            {formatElapsed(elapsedMs)}
          </span>
        )}
        <span className="flex-1 lg:hidden" />
        {transcriptCount > 0 && (
          <button
            type="button"
            data-testid="transcript-chip"
            aria-expanded={transcriptOpen}
            onClick={() => onTranscriptOpenChange?.(!transcriptOpen)}
            className="focus-visible:ring-ring rounded-xs focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none lg:hidden"
          >
            <StatusChip tone="neutral">
              <Icon as={MessageSquareTextIcon} size="sm" />
              {transcriptCount} messages
            </StatusChip>
          </button>
        )}
        {(agentState === "connecting" ||
          agentState === "reconnecting" ||
          agentState === "failed") && (
          <span data-testid="connection-chip">
          <StatusChip
            tone={
              agentState === "failed"
                ? "danger"
                : agentState === "reconnecting"
                  ? "warning"
                  : "neutral"
            }
            dot
          >
            {AGENT_STATE_LABEL[agentState]}
            {agentState === "failed" ? "" : "…"}
          </StatusChip>
          </span>
        )}
      </header>

      {banner}

      <div className={model.grid}>
        <section
          data-testid="session-stage"
          aria-label={`${agentName} stage`}
          className={model.stage}
        >
          {stage}
        </section>

        <aside
          data-testid="session-panel-column"
          aria-label={panelTitle}
          className={model.panel}
        >
          <header className="border-border flex shrink-0 items-center justify-between gap-2 border-b px-4 py-2.5">
            <h2 className="truncate text-sm font-semibold">{panelTitle}</h2>
            {panelStatus}
          </header>
          <div className="min-h-0 flex-1 overflow-auto">{panel}</div>
        </aside>

        <section
          data-testid="session-transcript"
          aria-label="Transcript"
          data-open={transcriptOpen ? "true" : "false"}
          className={model.transcript}
        >
          <header className="border-border flex shrink-0 items-center justify-between gap-2 border-b px-4 py-2.5">
            <h2 className="text-sm font-semibold">
              Transcript
              {transcriptCount > 0 && (
                <span className="text-muted-foreground ml-1.5 font-normal tabular-nums">
                  {transcriptCount}
                </span>
              )}
            </h2>
            <button
              type="button"
              aria-label="Hide transcript"
              onClick={() => onTranscriptOpenChange?.(false)}
              className="text-muted-foreground hover:text-foreground focus-visible:ring-ring -mr-1 inline-flex size-10 items-center justify-center rounded-sm focus-visible:ring-2 focus-visible:outline-none lg:hidden"
            >
              <Icon as={ChevronDownIcon} size="lg" />
            </button>
          </header>
          <div className="min-h-0 flex-1 overflow-hidden">{transcript}</div>
        </section>

        <div
          data-testid="session-controls"
          className={cn(model.controls, "flex justify-center")}
        >
          <div className="w-full max-w-2xl">{controls}</div>
        </div>
      </div>

      {/* Space for the fixed control bar below `lg` (safe-area aware). */}
      <div
        aria-hidden="true"
        className="h-[calc(88px+env(safe-area-inset-bottom,0px))] shrink-0 lg:hidden"
      />
    </div>
  );
}
