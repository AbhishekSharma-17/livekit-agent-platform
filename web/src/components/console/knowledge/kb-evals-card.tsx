"use client";

import * as React from "react";
import { toast } from "sonner";
import { PencilIcon, PlayIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard, StatGrid } from "@/components/shared/data-display";
import { LoadingRegion, LoadingRow } from "@/components/shared/loading-state";
import { LifecycleBadge } from "@/components/shared/status-chip";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { KbEvalDialog } from "@/components/console/knowledge/kb-eval-dialog";
import { plainStatusError } from "@/components/shared/status-error";
import { useWriteGate } from "@/components/console/shared/write-gate";
import {
  useEvaluateKb,
  useKbEvalRun,
  useKbEvals,
  useLatestKbEvalRun,
} from "@/components/console/lib/api-hooks";
import { pluralize } from "@/lib/format";
import type { KbEvalItemResult, KbEvalResult, KbEvalRunOut } from "@/contracts/lkap-contracts";

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

/** Found / Skipped / Missed, through the shared lifecycle map. */
function ItemStatusChip({ status }: { status: KbEvalItemResult["status"] }) {
  return <LifecycleBadge state={status === "found" || status === "skipped" ? status : "missed"} />;
}

function ResultSummary({ run, result }: { run: KbEvalRunOut; result: KbEvalResult }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-caption text-text-secondary tabular-nums">
          Last run {formatDate(result.finished_at)} · {result.mode === "hybrid" ? "Hybrid" : "Vector only"}
          {run.options.rerank === "local" ? " + re-ranked" : ""} · top {result.k}
        </p>
      </div>
      <StatGrid>
        <StatCard label="Recall@1" value={pct(result.recall_at_1)} hint="Right passage ranked first" />
        <StatCard label={`Recall@${result.k}`} value={pct(result.recall_at_k)} hint={`Right passage in the top ${result.k}`} />
        <StatCard label="MRR" value={result.mrr === null ? "—" : result.mrr.toFixed(2)} hint="How high it ranks, on average" />
        <StatCard
          label="Found"
          value={`${result.found} / ${result.scored}`}
          hint={result.skipped > 0 ? pluralize(result.skipped, "question skipped", "questions skipped") : "Questions answered"}
        />
      </StatGrid>
      {result.items && result.items.length > 0 ? (
        <details className="group/details">
          <summary className="cursor-pointer text-label font-medium text-text-secondary transition-colors duration-(--duration-fast) hover:text-foreground">
            Show every question
          </summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {result.items.map((item) => (
              <li
                key={item.eval_id}
                className="flex items-center justify-between gap-3 rounded border border-border px-3 py-2 text-body"
              >
                <span className="min-w-0 flex-1 truncate">{item.question}</span>
                <span className="flex shrink-0 items-center gap-2">
                  {item.status === "found" && item.rank ? (
                    <span className="text-caption tabular-nums text-text-secondary">rank {item.rank}</span>
                  ) : null}
                  <ItemStatusChip status={item.status} />
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/**
 * `kb-evals-card.tsx` (docs/v5/PLAN-V5.md V5-10): golden questions, an
 * "Evaluate" run and the last result's recall/MRR. The question set is
 * edited in `KbEvalDialog` (a dialog, never a side panel/sheet); running
 * enqueues a background job (`POST .../evaluate`) and this card polls it
 * until it finishes.
 */
export function KbEvalsCard({ kbId }: { kbId: string }) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [activeJobId, setActiveJobId] = React.useState<string | null>(null);

  const evalsQuery = useKbEvals(kbId);
  const latestRun = useLatestKbEvalRun(kbId);
  const activeRun = useKbEvalRun(kbId, activeJobId);
  const evaluate = useEvaluateKb(kbId);

  const total = evalsQuery.data?.total ?? 0;
  const running = activeRun.data ? activeRun.data.status === "pending" || activeRun.data.status === "running" : false;
  // The run just triggered this session wins over whatever was last saved; before that, the last finished run.
  const shown: KbEvalRunOut | null | undefined = activeJobId ? activeRun.data : latestRun.data;

  const gate = useWriteGate();

  async function handleRun() {
    try {
      const run = await evaluate.mutateAsync(undefined);
      setActiveJobId(run.job_id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Evaluate</h2>
        </CardTitle>
        <CardDescription className="tabular-nums">
          {total === 0 ? "No questions yet." : pluralize(total, "golden question", "golden questions")}
        </CardDescription>
        {gate.show ? (
          <CardAction className="flex-wrap">
            <Button type="button" variant="ghost" size="sm" disabled={gate.pending} onClick={() => setDialogOpen(true)}>
              <PencilIcon aria-hidden="true" /> Edit questions
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={gate.pending || total === 0}
              busy={evaluate.isPending || running}
              busyLabel="Running…"
              onClick={() => void handleRun()}
            >
              <PlayIcon aria-hidden="true" /> Run
            </Button>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {activeRun.data?.status === "failed" || activeRun.data?.status === "dead" ? (
          <ErrorBanner
            title="The evaluation run failed"
            message={plainStatusError(activeRun.data.error, "Something went wrong while scoring the questions. Run it again in a moment.")}
            onRetry={gate.can && total > 0 ? () => void handleRun() : undefined}
            retryLabel="Run again"
          />
        ) : null}

        {latestRun.isLoading && !activeJobId ? (
          <LoadingRegion label="Loading the last evaluation">
            <Skeleton className="mb-3 h-3.5 w-2/5" />
            <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-[104px] w-full rounded-lg" />
              ))}
            </div>
          </LoadingRegion>
        ) : shown?.result ? (
          <ResultSummary run={shown} result={shown.result} />
        ) : total === 0 ? (
          <EmptyState
            title="No golden questions yet"
            description="Add a few questions with a known answer, then run Evaluate to see how well retrieval finds them."
            compact
          />
        ) : running ? (
          <LoadingRow label="Running the evaluation set…" />
        ) : (
          <p className="text-label text-text-secondary">Run the evaluation set to see recall and MRR.</p>
        )}
      </CardContent>

      <KbEvalDialog kbId={kbId} open={dialogOpen} onOpenChange={setDialogOpen} evals={evalsQuery.data?.items ?? []} />
    </Card>
  );
}
