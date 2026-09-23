"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { useTestCredential } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { cn } from "@/lib/utils";

/** How long a test may run before the UI gives up (§6 "Long operations"). */
export const CREDENTIAL_TEST_TIMEOUT_MS = 10_000;

export type CredentialTestOutcome = "passed" | "failed" | "not-implemented" | "timed-out";

export interface CredentialTestRecord {
  outcome: CredentialTestOutcome;
  message: string;
  /** `CredentialTestResult.checked_at` when the api sends it, else the client clock. */
  testedAt: string;
  /** Client clock (ms) when the result arrived — drives the page's 10 s result chip. */
  receivedAt: number;
}

const testKey = (id: string) => ["credential-test", id] as const;

/**
 * `CredentialTestResult` → one of four outcomes. Avatar providers may
 * answer "not implemented" (CONTRACTS §7) — that is neutral, not a failure.
 */
export function classifyTestResult(result: { ok: boolean; message: string }): CredentialTestOutcome {
  if (/not implemented/i.test(result.message)) return "not-implemented";
  return result.ok ? "passed" : "failed";
}

export const OUTCOME_TONE: Record<CredentialTestOutcome, StatusTone> = {
  passed: "success",
  failed: "danger",
  "not-implemented": "neutral",
  "timed-out": "danger",
};

export const OUTCOME_LABEL: Record<CredentialTestOutcome, string> = {
  passed: "Key works",
  failed: "Test failed",
  "not-implemented": "No test for this provider",
  "timed-out": "Timed out — try again",
};

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | "timeout"> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve("timeout"), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

interface CredentialTestState {
  last: CredentialTestRecord | null;
  pending: boolean;
}

const IDLE: CredentialTestState = { last: null, pending: false };

/**
 * Run `POST /v1/credentials/{id}/test` and remember the last result.
 *
 * `CredentialOut` has no "last tested" field, so the state is kept in the
 * react-query cache under `["credential-test", id]` for this browser
 * session: every component showing the same key (the credentials page's
 * row menu and result cell, the sheet, the slot's picker) sees the same
 * pending flag and result. When the api persists a last-tested time
 * (V2-06), read it here.
 */
export function useCredentialTest(id: string | null | undefined) {
  const queryClient = useQueryClient();
  const key = testKey(id ?? "");
  const { data } = useQuery<CredentialTestState>({
    queryKey: key,
    queryFn: () => queryClient.getQueryData<CredentialTestState>(key) ?? IDLE,
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const { mutateAsync } = useTestCredential();
  const state = data ?? IDLE;

  const run = React.useCallback(async (): Promise<CredentialTestRecord | null> => {
    if (!id) return null;
    const cacheKey = testKey(id);
    const previous = queryClient.getQueryData<CredentialTestState>(cacheKey)?.last ?? null;
    queryClient.setQueryData<CredentialTestState>(cacheKey, { last: previous, pending: true });
    let record: CredentialTestRecord;
    const now = () => new Date().toISOString();
    try {
      const result = await withTimeout(mutateAsync(id), CREDENTIAL_TEST_TIMEOUT_MS);
      if (result === "timeout") {
        record = { outcome: "timed-out", message: "The vendor didn't answer within 10 seconds.", testedAt: now(), receivedAt: Date.now() };
      } else {
        record = {
          outcome: classifyTestResult(result),
          message: result.message,
          testedAt: result.checked_at ?? now(),
          receivedAt: Date.now(),
        };
      }
    } catch (error) {
      record = { outcome: "failed", message: errorMessage(error), testedAt: now(), receivedAt: Date.now() };
    }
    queryClient.setQueryData<CredentialTestState>(cacheKey, { last: record, pending: false });
    return record;
  }, [id, mutateAsync, queryClient]);

  return { last: state.last, pending: state.pending, run };
}

/** Result chip + vendor message + "Last tested …" (the sheet's persistent view). */
export function CredentialTestResultView({
  record,
  pending,
  className,
}: {
  record: CredentialTestRecord | null;
  pending: boolean;
  className?: string;
}) {
  return (
    <div role="status" aria-live="polite" className={cn("flex flex-col gap-1.5", className)}>
      {pending ? (
        <span className="text-[0.8125rem] text-muted-foreground">Testing…</span>
      ) : record ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip tone={OUTCOME_TONE[record.outcome]} dot size="sm">
              {OUTCOME_LABEL[record.outcome]}
            </StatusChip>
            <span className="text-xs text-muted-foreground">
              Last tested <RelativeTime iso={record.testedAt} />
            </span>
          </div>
          {record.message ? (
            <p className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">{record.message}</p>
          ) : null}
        </>
      ) : (
        <span className="text-[0.8125rem] text-muted-foreground">Not tested yet.</span>
      )}
    </div>
  );
}
