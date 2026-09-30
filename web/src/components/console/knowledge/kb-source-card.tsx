"use client";

import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/shared/status-chip";
import { RelativeTime } from "@/components/shared/relative-time";
import { useKbSource } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { pluralize } from "@/lib/format";

/**
 * A managed search knowledge base (Ragie, V5-45, `kb.kind === "external"`)
 * has no documents here — `kb-detail.tsx` renders this in place of
 * `KbDocuments` (upload/import/re-index don't apply; the api already refuses
 * them with 409 — this is presentation only, docs/v5/_asks.md #232). Reads
 * `GET /v1/knowledge-bases/{id}/source` (`KbSourceOut`) for the partition's
 * document count and, when the vendor reports one, the last sync time.
 */
export function KbSourceCard({ kbId, externalRef }: { kbId: string; externalRef?: string | null }) {
  const { data, isLoading, isError, error, refetch } = useKbSource(kbId);

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-body font-semibold text-foreground">Source</h2>
        <p className="text-label text-text-secondary">
          Documents stay in Ragie{externalRef ? ` — partition ${externalRef}` : ""}. Nothing is uploaded here.
        </p>
      </div>
      {isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : isError || !data ? (
        <ErrorBanner message={`Couldn't reach Ragie — ${errorMessage(error)}`} onRetry={() => refetch()} />
      ) : (
        <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={data.ok ? "success" : "danger"} size="sm">
              {data.ok ? "Connected" : "Problem"}
            </StatusPill>
            {data.message ? <span className="text-label text-pretty text-text-secondary">{data.message}</span> : null}
          </div>
          <p className="text-body text-foreground">
            {data.document_count === null || data.document_count === undefined
              ? "Document count unavailable."
              : pluralize(data.document_count, "document", "documents")}
          </p>
          {data.last_synced_at ? (
            <p className="text-caption text-text-secondary">
              Last synced <RelativeTime iso={data.last_synced_at} />
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}
