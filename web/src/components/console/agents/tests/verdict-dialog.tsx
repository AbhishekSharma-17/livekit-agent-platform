"use client";

/**
 * `verdict-dialog.tsx` (V5-33): one case's outcome within a run — the five
 * judge scores, the simulated transcript and the tool calls (mocked ones
 * marked), in a dialog (never a side drawer).
 */
import * as React from "react";

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { DetailsDisclosure, CodeBlock } from "@/components/console/sessions/details-disclosure";
import { cn } from "@/lib/utils";
import type { AgentTestJudgeScore, AgentTestVerdict } from "@/contracts/lkap-contracts";

const JUDGE_LABEL: Record<AgentTestJudgeScore["judge"], string> = {
  task_completion: "Task completion",
  tool_use: "Tool use",
  safety: "Safety",
  relevancy: "Relevancy",
  accuracy: "Accuracy",
};

const VERDICT_TONE: Record<AgentTestJudgeScore["verdict"], StatusTone> = {
  pass: "success",
  fail: "danger",
  inconclusive: "neutral",
};

const CASE_STATUS_TONE: Record<AgentTestVerdict["status"], StatusTone> = {
  passed: "success",
  failed: "danger",
  inconclusive: "neutral",
  error: "warning",
};

const CASE_STATUS_LABEL: Record<AgentTestVerdict["status"], string> = {
  passed: "Passed",
  failed: "Failed",
  inconclusive: "Inconclusive",
  // "error" reads as "the case didn't run", never "failed" — mirrors the publish gate's own wording rule.
  error: "Didn't run",
};

const STOPPED_BY_LABEL: Record<NonNullable<AgentTestVerdict["stopped_by"]>, string> = {
  persona_done: "The caller said they were finished",
  max_turns: "Reached the turn budget",
  agent_timeout: "The agent didn't answer in time",
  disconnected: "The conversation was disconnected",
  error: "Something went wrong",
};

export interface VerdictDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  verdict: AgentTestVerdict | null;
}

export function VerdictDialog({ open, onOpenChange, verdict }: VerdictDialogProps) {
  if (!verdict) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            {verdict.case_name}
            <StatusChip tone={CASE_STATUS_TONE[verdict.status]}>{CASE_STATUS_LABEL[verdict.status]}</StatusChip>
          </DialogTitle>
          <DialogDescription>
            {verdict.turns} {verdict.turns === 1 ? "turn" : "turns"}
            {verdict.stopped_by ? ` · stopped: ${STOPPED_BY_LABEL[verdict.stopped_by]}` : ""}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          {verdict.error ? (
            <p className="rounded bg-warning-subtle px-3 py-2 text-label text-warning-text">{verdict.error}</p>
          ) : null}

          {(verdict.scores ?? []).length > 0 ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {(verdict.scores ?? []).map((score) => (
                <div key={score.judge} className="flex flex-col gap-1 rounded border border-border p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{JUDGE_LABEL[score.judge]}</span>
                    <StatusChip tone={VERDICT_TONE[score.verdict]} size="sm">
                      {score.verdict}
                    </StatusChip>
                  </div>
                  {score.reason ? <p className="text-label text-pretty text-text-secondary">{score.reason}</p> : null}
                </div>
              ))}
            </div>
          ) : null}

          {(verdict.transcript ?? []).length > 0 ? (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold">Transcript</h3>
              <ol className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-1">
                {(verdict.transcript ?? []).map((turn, index) => (
                  <li key={index} className={cn("flex", turn.role === "user" && "justify-end")}>
                    <p
                      className={cn(
                        "max-w-[85%] rounded-lg px-3 py-1.5 text-sm leading-snug break-words",
                        turn.role === "user" ? "bg-muted" : "bg-brand-subtle text-foreground",
                      )}
                    >
                      <span className="sr-only">{turn.role === "user" ? "Persona: " : "Agent: "}</span>
                      {turn.text}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          {(verdict.tool_calls ?? []).length > 0 ? (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold">Tool calls</h3>
              <ul className="flex flex-col gap-1.5">
                {(verdict.tool_calls ?? []).map((call, index) => (
                  <li key={index} className="flex flex-wrap items-center gap-2 text-label">
                    <span className="font-mono">{call.tool}</span>
                    {call.mocked ? (
                      <StatusChip tone="info" size="sm">
                        Mocked
                      </StatusChip>
                    ) : null}
                    {call.status ? <span className="text-text-secondary">{call.status}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <DetailsDisclosure label="Raw verdict">
            <CodeBlock value={JSON.stringify(verdict, null, 2)} />
          </DetailsDisclosure>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
