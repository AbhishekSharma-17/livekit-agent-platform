"use client";

import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { StatusPill } from "@/components/shared/status-chip";
import { RelativeTime } from "@/components/shared/relative-time";
import { useKbSource } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { plainStatusError } from "@/components/console/knowledge/status-error";
import { pluralize } from "@/lib/format";

/**
 * A managed search knowledge base (Ragie, V5-45, `kb.kind === "external"`)
 * has no documents here — `kb-detail.tsx` renders this in place of
 * `KbDocuments` (upload/import/re-index don't apply; the api already refuses
 * them with 409 — this is presentation only, docs/v5/_asks.md #232). Reads
 * `GET /v1/knowledge-bases/{id}/source` (`KbSourceOut`) for the partition's
 * document count and, when the vendor reports one, the last sync time.
 * Ragie's own status message is shown only when it reads as plain copy.
 */
export function KbSourceCard({ kbId, externalRef }: { kbId: string; externalRef?: string | null }) {
  const { data, isLoading, isError, error, refetch } = useKbSource(kbId);

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Source</h2>
        </CardTitle>
        <CardDescription>
          Documents stay in Ragie{externalRef ? `, in the “${externalRef}” partition` : ""}. Nothing is uploaded here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingRegion label="Checking Ragie" className="flex flex-col gap-2">
            <Skeleton className="h-[22px] w-24 rounded-pill" />
            <Skeleton className="h-4 w-32" />
          </LoadingRegion>
        ) : isError || !data ? (
          <ErrorBanner title="Couldn't reach Ragie" error={error} onRetry={() => refetch()} />
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={data.ok ? "success" : "danger"} size="sm">
                {data.ok ? "Connected" : "Problem"}
              </StatusPill>
              {data.message ? (
                <span className="text-label text-pretty text-text-secondary">
                  {plainStatusError(data.message, "Ragie didn't say what went wrong. Check the connection in Settings.")}
                </span>
              ) : null}
            </div>
            <p className="text-body text-foreground tabular-nums">
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
      </CardContent>
    </Card>
  );
}
