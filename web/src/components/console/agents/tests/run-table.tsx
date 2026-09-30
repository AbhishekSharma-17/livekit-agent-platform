"use client";

/**
 * `run-table.tsx` (V5-33): the agent's test-run history, newest first — no
 * verdicts (the list route doesn't carry them); selecting a row loads them
 * (`tests-section.tsx` polls `useAgentTestRun` for the selected id).
 */
import * as React from "react";

import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentTestRun } from "@/contracts/lkap-contracts";

const RUN_STATUS_TONE: Record<AgentTestRun["status"], StatusTone> = {
  queued: "neutral",
  running: "info",
  passed: "success",
  failed: "danger",
  inconclusive: "warning",
  error: "warning",
};

const RUN_STATUS_LABEL: Record<AgentTestRun["status"], string> = {
  queued: "Queued",
  running: "Running…",
  passed: "Passed",
  failed: "Failed",
  inconclusive: "Inconclusive",
  // Never "failed": the tests didn't run (docs/v5/PLAN-V5.md V5-33, `agent_tests.py`).
  error: "Didn't run",
};

function summary(run: AgentTestRun): string {
  if (run.status === "queued" || run.status === "running") return `${(run.case_ids ?? []).length} cases`;
  if (run.status === "error") return run.error ?? "The run couldn't run";
  const total = run.case_ids?.length ?? (run.passed ?? 0) + (run.failed ?? 0) + (run.inconclusive ?? 0) + (run.errored ?? 0);
  return `${run.passed ?? 0} of ${pluralize(total, "case", "cases")} passed`;
}

export interface RunTableProps {
  runs: AgentTestRun[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

export function RunTable({ runs, selectedRunId, onSelect }: RunTableProps) {
  if (runs.length === 0) {
    return <p className="text-sm text-text-secondary">No runs yet — pick Run to try the cases above.</p>;
  }
  return (
    <ul className="flex flex-col gap-1.5" aria-label="Test runs">
      {runs.map((run) => (
        <li key={run.id}>
          <button
            type="button"
            onClick={() => onSelect(run.id)}
            aria-pressed={selectedRunId === run.id}
            className={cn(
              "flex w-full flex-wrap items-center justify-between gap-2 rounded border border-border px-3 py-2 text-left transition-colors duration-(--duration-base) hover:bg-muted",
              selectedRunId === run.id && "border-brand-border bg-brand-subtle/40",
            )}
          >
            <span className="flex flex-wrap items-center gap-2">
              <StatusChip tone={RUN_STATUS_TONE[run.status]}>{RUN_STATUS_LABEL[run.status]}</StatusChip>
              <span className="text-label text-text-secondary">{summary(run)}</span>
            </span>
            <span className="text-xs text-text-secondary">
              <RelativeTime iso={run.created_at} />
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
