"use client";

/**
 * Memory tab (V5-42, ask #263): what this session recalled at the start of
 * the call and what was stored after it ended, plus "Forget this caller".
 * `GET /v1/sessions/{id}/memory` (`SessionMemoryOut`) is a separate route —
 * not embedded on `SessionDetailOut` — so this tab fetches it itself, the
 * way `SessionTabProps`'s own contract says a tab should ("tabs fetch
 * anything else they need themselves").
 *
 * Recalled and stored memories are third-party text (derived from what a
 * caller said, R-V5-15): shown as plain, wrapping, monospace text that is
 * never interpreted as markup — the same `UntrustedBox` convention
 * `registry/model-test-panel.tsx` uses for vendor text.
 *
 * "Forget this caller" needs `admin` (the api's `/v1/memory` route policy,
 * ask #258: `Requirement("admin", "sessions:write")`) — disabled with a
 * reason for anyone else, never just hidden, so a builder or viewer can see
 * the action exists and who to ask. A 503 `memory_unavailable` (the memory
 * add-on isn't installed on this server) is shown as a plain notice, never
 * a raw error — the card's own rule for an optional server extra.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainIcon, UserXIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { RelativeTime } from "@/components/shared/relative-time";
import { SkeletonRows } from "@/components/shared/loading-state";
import { StatusPill } from "@/components/shared/status-chip";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { useWriteAccess } from "@/components/console/lib/roles";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";
import { ApiError, api } from "@/lib/api";
import type { MemoryForgetOut, SessionMemoryOut } from "@/contracts/lkap-contracts";

const MEMORY_UNAVAILABLE_NOTICE = "Memory is not installed on this server.";

/** `SessionMemoryOut.recall_status`, in plain words. */
const RECALL_TEXT: Record<NonNullable<SessionMemoryOut["recall_status"]>, string> = {
  recalled: "Recalled from an earlier call.",
  empty: "Nothing to recall — this looks like the caller's first call.",
  disabled: "Memory was off for this agent when the call started.",
  no_identity: "This caller was anonymous, so there was nothing to recall.",
  unavailable: MEMORY_UNAVAILABLE_NOTICE,
  failed: "Recalling memories failed for this call.",
};

/** `SessionMemoryOut.store_status`, in plain words. */
const STORE_TEXT: Record<NonNullable<SessionMemoryOut["store_status"]>, string> = {
  stored: "Stored after the call.",
  nothing_new: "Nothing new was worth storing.",
  skipped: "Nothing was stored.",
  failed: "Storing failed after the call.",
};

/** Third-party text, shown as data (R-V5-15): a mono, wrapping list that never interprets markup. */
function UntrustedList({
  label,
  items,
  empty,
}: {
  label: string;
  items: string[];
  empty: React.ReactNode;
}) {
  if (items.length === 0) {
    return <p className="text-label text-text-secondary">{empty}</p>;
  }
  return (
    <ul
      data-slot="untrusted-text"
      aria-label={label}
      className="flex flex-col gap-1 rounded border border-border bg-muted p-2"
    >
      {items.map((item, index) => (
        <li key={index} className="font-mono text-caption break-words whitespace-pre-wrap text-foreground">
          {item}
        </li>
      ))}
    </ul>
  );
}

function memoryUnavailableFrom(err: unknown): boolean {
  return err instanceof ApiError && err.code === "memory_unavailable";
}

export function MemoryTab({ session }: SessionTabProps) {
  const queryClient = useQueryClient();
  const { canWrite: canForget } = useWriteAccess("admin");

  const memoryQuery = useQuery({
    queryKey: ["sessions", session.id, "memory"] as const,
    queryFn: () => api.get<SessionMemoryOut>(`sessions/${session.id}/memory`),
  });

  const forget = useMutation({
    mutationFn: (subjectId: string) => api.delete<MemoryForgetOut>(`memory/subjects/${subjectId}`),
    onSuccess: (result) => {
      toast.success(
        result.forgotten
          ? `Caller forgotten — ${result.sessions_updated ?? 0} session${result.sessions_updated === 1 ? "" : "s"} updated.`
          : "Nothing was stored for this caller.",
      );
      // A prefix match on ["sessions", session.id] also invalidates this tab's own
      // ["sessions", session.id, "memory"] query, the session detail, and its events query (the
      // qa-tab.tsx pattern) — so the new `memory_forgotten` timeline row shows up too, without a
      // manual reload.
      void queryClient.invalidateQueries({ queryKey: ["sessions", session.id] });
    },
    onError: (err: unknown) => {
      toast.error(memoryUnavailableFrom(err) ? MEMORY_UNAVAILABLE_NOTICE : `Couldn't forget this caller. ${errorMessage(err)}`);
    },
  });

  if (memoryQuery.isLoading) {
    return <SkeletonRows label="Loading memory" rows={3} rowClassName="h-9" />;
  }

  if (memoryQuery.isError) {
    if (memoryUnavailableFrom(memoryQuery.error)) {
      return <EmptyState icon={BrainIcon} title="Memory isn't installed" description={MEMORY_UNAVAILABLE_NOTICE} />;
    }
    return (
      <ErrorBanner
        error={memoryQuery.error}
        context={{ action: "load memory" }}
        onRetry={() => void memoryQuery.refetch()}
      />
    );
  }

  const memory = memoryQuery.data;
  if (!memory) return null;

  if (!memory.enabled) {
    return (
      <EmptyState
        icon={BrainIcon}
        title="Memory is off for this agent"
        description="Turn on “Remember returning callers” in the agent's Memory section to remember this caller between calls."
      />
    );
  }

  const recallStatus = memory.recall_status ?? null;
  const storeStatus = memory.store_status ?? null;
  const forgotten = Boolean(memory.forgotten_at);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill tone="neutral" size="sm">
          {memory.subject_id ? "Pseudonymous caller id known" : "No caller id for this session"}
        </StatusPill>
        {forgotten ? (
          <span className="text-caption text-text-secondary">
            Forgotten <RelativeTime iso={memory.forgotten_at as string} />
          </span>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <h3 className="text-body font-semibold text-foreground">What the agent recalled at the start of the call</h3>
        {recallStatus ? <p className="text-label text-text-secondary">{RECALL_TEXT[recallStatus]}</p> : null}
        <UntrustedList
          label="Recalled memories"
          items={memory.recalled ?? []}
          empty="Nothing was recalled."
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <h3 className="text-body font-semibold text-foreground">What was stored after the call</h3>
        {storeStatus ? (
          <p className="text-label text-text-secondary">
            {STORE_TEXT[storeStatus]}
            {memory.store_reason ? ` — ${memory.store_reason}.` : ""}
          </p>
        ) : (
          <p className="text-label text-text-secondary">Nothing has been stored for this call yet.</p>
        )}
        <UntrustedList label="Stored memories" items={memory.stored ?? []} empty="Nothing was stored." />
      </div>

      {memory.subject_id && !forgotten ? (
        <div>
          {canForget ? (
            <ConfirmDialog
              trigger={
                <Button type="button" variant="danger-outline" size="sm">
                  <Icon as={UserXIcon} size="sm" />
                  Forget this caller
                </Button>
              }
              title="Forget this caller?"
              description="Deletes everything remembered about this caller everywhere it's shared, and blanks the memories recorded on their past sessions. This can't be undone."
              confirmLabel="Forget caller"
              onConfirm={async () => {
                await forget.mutateAsync(memory.subject_id as string);
              }}
            />
          ) : (
            // D12: a person who can't forget callers reads the next step instead of a disabled button.
            <ReadOnlyNote>{readOnlyCopy("admin", "forget this caller")}</ReadOnlyNote>
          )}
        </div>
      ) : null}
    </div>
  );
}
