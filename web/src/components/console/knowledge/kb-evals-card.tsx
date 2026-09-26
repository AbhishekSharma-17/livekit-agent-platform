"use client";

import * as React from "react";
import { toast } from "sonner";
import { Loader2Icon, PencilIcon, PlayIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { KbEvalDialog } from "@/components/console/knowledge/kb-eval-dialog";
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

function ItemStatusChip({ status }: { status: KbEvalItemResult["status"] }) {
  if (status === "found") return <StatusChip tone="success">Found</StatusChip>;
  if (status === "skipped") return <StatusChip tone="neutral">Skipped</StatusChip>;
  return <StatusChip tone="danger">Missed</StatusChip>;
}

function ResultSummary({ run, result }: { run: KbEvalRunOut; result: KbEvalResult }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Last run {formatDate(result.finished_at)} · {result.mode === "hybrid" ? "Hybrid" : "Vector only"}
          {run.options.rerank === "local" ? " + re-ranked" : ""} · top {result.k}
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Recall@1</dt>
          <dd className="text-lg font-semibold tabular-nums text-foreground">{pct(result.recall_at_1)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Recall@{result.k}</dt>
          <dd className="text-lg font-semibold tabular-nums text-foreground">{pct(result.recall_at_k)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">MRR</dt>
          <dd className="text-lg font-semibold tabular-nums text-foreground">
            {result.mrr === null ? "—" : result.mrr.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Found</dt>
          <dd className="text-lg font-semibold tabular-nums text-foreground">
            {result.found} / {result.scored}
            {result.skipped > 0 ? (
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                ({pluralize(result.skipped, "skipped", "skipped")})
              </span>
            ) : null}
          </dd>
        </div>
      </dl>
      {result.items && result.items.length > 0 ? (
        <details className="group/details">
          <summary className="cursor-pointer text-[0.8125rem] font-medium text-muted-foreground hover:text-foreground">
            Show every question
          </summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {result.items.map((item) => (
              <li
                key={item.eval_id}
                className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm"
              >
                <span className="min-w-0 flex-1 truncate">{item.question}</span>
                <span className="flex shrink-0 items-center gap-2">
                  {item.status === "found" && item.rank ? (
                    <span className="text-xs tabular-nums text-muted-foreground">rank {item.rank}</span>
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

  async function handleRun() {
    try {
      const run = await evaluate.mutateAsync(undefined);
      setActiveJobId(run.job_id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Evaluate</h2>
          <p className="text-[0.8125rem] text-muted-foreground">
            {total === 0 ? "No questions yet." : pluralize(total, "golden question", "golden questions")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
            <Icon as={PencilIcon} size="sm" /> Edit questions
          </Button>
          <Button type="button" size="sm" disabled={total === 0 || evaluate.isPending || running} onClick={() => void handleRun()}>
            {evaluate.isPending || running ? (
              <>
                <Icon as={Loader2Icon} size="sm" className="animate-spin" /> Running…
              </>
            ) : (
              <>
                <Icon as={PlayIcon} size="sm" /> Run
              </>
            )}
          </Button>
        </div>
      </div>

      {activeRun.data?.status === "failed" || activeRun.data?.status === "dead" ? (
        <ErrorBanner message={activeRun.data.error ?? "The evaluation run failed."} />
      ) : null}

      {latestRun.isLoading && !activeJobId ? (
        <Skeleton className="h-16 w-full" />
      ) : shown?.result ? (
        <ResultSummary run={shown} result={shown.result} />
      ) : total === 0 ? (
        <EmptyState
          title="No golden questions yet"
          description="Add a few questions with a known answer, then run Evaluate to see how well retrieval finds them."
          compact
        />
      ) : running ? (
        <p className="text-sm text-muted-foreground">Running the evaluation set…</p>
      ) : (
        <p className="text-sm text-muted-foreground">Run the evaluation set to see recall and MRR.</p>
      )}

      <KbEvalDialog kbId={kbId} open={dialogOpen} onOpenChange={setDialogOpen} evals={evalsQuery.data?.items ?? []} />
    </div>
  );
}
