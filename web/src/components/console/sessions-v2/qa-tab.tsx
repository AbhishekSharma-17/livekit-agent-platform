"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GaugeIcon, RotateCwIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { DetailsDisclosure, CodeBlock } from "@/components/console/sessions/details-disclosure";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";
import { ApiError, api } from "@/lib/api";
import type { QaOut, SessionScrubOut } from "@/contracts/lkap-contracts";

/**
 * QA tab (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.4): score, sentiment, tags,
 * summary and a "Re-score" action. `QaOut` (CONTRACTS-V2 §1.4/§3.4) has no
 * `error` field, so a `failed` status has nothing more specific to show than
 * "Scoring failed" — the api's own failure reasons live server-side.
 * `POST /v1/sessions/{id}/qa` 409s `qa_judge_unavailable` (R-V2-5) for an
 * Inference-only judge; that message names the fix ("set `qa.model` to a
 * vendor-key provider"), so it's shown verbatim rather than paraphrased.
 */
const SENTIMENT_TONE: Record<string, StatusTone> = { positive: "success", neutral: "neutral", negative: "danger" };

function scoreTone(score: number | null | undefined): StatusTone {
  if (score == null) return "neutral";
  if (score >= 8) return "success";
  if (score >= 5) return "warning";
  return "danger";
}

/** Prettifies a `QaField.name` ("claim_type" -> "Claim type") for the fields list below. */
function fieldLabel(name: string): string {
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function fieldValueText(value: unknown): string {
  if (value === null || value === undefined) return "Not mentioned";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

/**
 * The privacy cleanup row (V5-34): `session.scrubbed_at` once the post-call
 * scrub has run, else a manual "Clean up now" action
 * (`POST /v1/sessions/{id}/scrub`) for a session that ended before its
 * agent's tier was chosen (or before the scrub-on-summary hookup landed).
 * The api's own 409 messages ("this agent keeps sessions in full…", "the
 * session has not ended yet") are shown verbatim — they already name the fix.
 */
function ScrubRow({ session }: SessionTabProps) {
  const queryClient = useQueryClient();
  const scrub = useMutation({
    mutationFn: () => api.post<SessionScrubOut>(`sessions/${session.id}/scrub`),
    onSuccess: (result) => {
      if (result.status === "already_scrubbed") toast.info("Already cleaned up");
      else toast.success("Cleanup queued");
      void queryClient.invalidateQueries({ queryKey: ["sessions", session.id] });
    },
    onError: (err: unknown) => toast.error(err instanceof ApiError ? err.message : "Couldn't clean up this session"),
  });

  if (session.scrubbed_at) {
    return (
      <p className="text-[0.8125rem] text-muted-foreground">
        Personal details cleaned up <RelativeTime iso={session.scrubbed_at} />.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-[0.8125rem] text-muted-foreground">
        Not cleaned up yet. This normally happens by itself once the call ends.
      </p>
      <Button type="button" variant="outline" size="sm" onClick={() => scrub.mutate()} disabled={scrub.isPending}>
        {scrub.isPending ? "Cleaning up…" : "Clean up now"}
      </Button>
    </div>
  );
}

export function QaTab({ session }: SessionTabProps) {
  const queryClient = useQueryClient();
  const qa = session.qa;
  const status = qa?.status ?? "pending";

  const rescore = useMutation({
    mutationFn: () => api.post<QaOut>(`sessions/${session.id}/qa`),
    onSuccess: () => {
      toast.success("Re-score requested");
      queryClient.invalidateQueries({ queryKey: ["sessions", session.id] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.code === "qa_judge_unavailable") {
        toast.error(err.message);
      } else {
        toast.error(err instanceof ApiError ? err.message : "Couldn't re-score this session");
      }
    },
  });

  // A `pending` QA row (still being scored) polls quietly until it settles.
  React.useEffect(() => {
    if (status !== "pending") return;
    const id = setInterval(() => queryClient.invalidateQueries({ queryKey: ["sessions", session.id] }), 5000);
    return () => clearInterval(id);
  }, [status, session.id, queryClient]);

  const rescoreButton = (
    <Button type="button" variant="outline" size="sm" onClick={() => rescore.mutate()} disabled={rescore.isPending}>
      <Icon as={RotateCwIcon} size="sm" className={rescore.isPending ? "animate-spin" : undefined} />
      Re-score
    </Button>
  );

  const fieldEntries = Object.entries(qa?.fields ?? {});

  let body: React.ReactNode;
  if (!qa || status === "skipped") {
    body = (
      <EmptyState
        icon={GaugeIcon}
        title={status === "skipped" ? "Not scored" : "No QA data"}
        description={
          status === "skipped"
            ? "QA is turned off for this agent, or the call had no conversation to score."
            : "This session hasn't been scored yet."
        }
        action={rescoreButton}
      />
    );
  } else if (status === "pending") {
    body = <EmptyState icon={GaugeIcon} title="Scoring in progress…" description="This updates automatically." />;
  } else if (status === "failed") {
    body = (
      <EmptyState
        icon={GaugeIcon}
        title="Scoring failed"
        description="The judge model couldn't produce a verdict for this call."
        action={rescoreButton}
      />
    );
  } else {
    body = (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip tone={scoreTone(qa.score)}>{qa.score != null ? `Score ${qa.score}/10` : "No score"}</StatusChip>
          {qa.sentiment ? (
            <StatusChip tone={SENTIMENT_TONE[qa.sentiment] ?? "neutral"}>{qa.sentiment}</StatusChip>
          ) : null}
          {qa.model ? <span className="font-mono text-xs text-muted-foreground">{qa.model}</span> : null}
          <span className="text-xs text-muted-foreground">
            {qa.scored_by === "worker" ? "Scored at call end" : qa.scored_by === "api" ? "Re-scored" : null}
          </span>
          <div className="ml-auto">{rescoreButton}</div>
        </div>
        {qa.tags && qa.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {qa.tags.map((tag) => (
              <StatusChip key={tag} tone="neutral" size="sm">
                {tag}
              </StatusChip>
            ))}
          </div>
        ) : null}
        {qa.summary ? <p className="max-w-[70ch] text-sm text-pretty text-foreground">{qa.summary}</p> : null}
        {fieldEntries.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <h3 className="text-sm font-semibold text-foreground">Post-call fields</h3>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {fieldEntries.map(([name, value]) => (
                <div key={name} className="flex items-baseline justify-between gap-2 text-sm">
                  <dt className="text-muted-foreground">{fieldLabel(name)}</dt>
                  <dd className="text-right text-foreground">{fieldValueText(value)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}
        <DetailsDisclosure label="Raw verdict">
          <CodeBlock value={JSON.stringify(qa, null, 2)} />
        </DetailsDisclosure>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <ScrubRow session={session} />
      {body}
    </div>
  );
}
