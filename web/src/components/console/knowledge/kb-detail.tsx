"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { UploadIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeleteKb, useKb, useKnowledgeConnections, useProviders } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import { knowledgeConnectionKindLabel } from "@/components/console/settings/knowledge-connection-dialog";
import { DangerZoneCard } from "@/components/console/shared/danger-zone-card";
import { KbDocuments, type KbDocumentsHandle } from "@/components/console/knowledge/kb-documents";
import { KbSourceCard } from "@/components/console/knowledge/kb-source-card";
import { KbEvalsCard } from "@/components/console/knowledge/kb-evals-card";
import { KbSearchPanel } from "@/components/console/knowledge/kb-search-panel";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { DescriptionList } from "@/components/shared/description-list";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Page, PageHeader } from "@/components/shared/page-header";
import { Tag } from "@/components/shared/tag";
import { pluralize } from "@/lib/format";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import type { KbOut } from "@/contracts/lkap-contracts";

const BACK = { href: "/console/knowledge", label: "Back to knowledge" };

/**
 * `/console/knowledge/{id}` as the detail archetype (docs/ui/DESIGN-SYSTEM.md
 * section 7.4): a back link, the name (and a "Managed search" tag), one
 * sentence, and the one primary — Upload documents — for a knowledge base
 * that holds its own documents. The main column holds the work (documents or
 * the Ragie source, test search, evaluation); the side column holds the facts
 * and, for builders, the Danger zone.
 */
export function KbDetail({ kbId }: { kbId: string }) {
  const { data: kb, isLoading, isError, error, refetch } = useKb(kbId);
  const connectionsQuery = useKnowledgeConnections();
  const providersQuery = useProviders();
  const gate = useWriteGate();
  const documentsRef = React.useRef<KbDocumentsHandle>(null);

  if (isLoading) {
    return <KbDetailSkeleton />;
  }

  if (isError || !kb) {
    return (
      <Page width="wide">
        <PageHeader back={BACK} title="Knowledge base" />
        <ErrorBanner error={error} context={{ action: "load this knowledge base" }} onRetry={() => refetch()} />
      </Page>
    );
  }

  const connection = kb.connection_id
    ? (connectionsQuery.data?.items ?? []).find((item) => item.id === kb.connection_id)
    : null;
  const storedIn = kb.connection_id
    ? connection
      ? `${connection.name} (${knowledgeConnectionKindLabel(connection.kind, providersQuery.data?.providers ?? [])})`
      : "A knowledge connection"
    : "This platform";
  // Managed search (Ragie, V5-45): no documents live here, so the usual
  // Embedder/Chunks facts and the upload/import/re-index controls don't
  // apply (the api already refuses them with 409 — ask #232 is presentation
  // only). Its own source panel replaces `KbDocuments` below.
  const isExternal = kb.kind === "external";

  return (
    <Page width="wide">
      <ConsoleBreadcrumbs trail={[{ label: "Knowledge", href: "/console/knowledge" }, { label: kb.name }]} />
      <PageHeader
        back={BACK}
        title={kb.name}
        badge={isExternal ? <Tag>Managed search</Tag> : undefined}
        description={
          kb.description ||
          (isExternal
            ? "Agents search these documents in Ragie during a call."
            : "Documents agents search for answers during a call.")
        }
        actions={
          !isExternal && gate.show ? (
            <Button type="button" variant="primary" disabled={gate.pending} onClick={() => documentsRef.current?.openFilePicker()}>
              <UploadIcon aria-hidden="true" /> Upload documents
            </Button>
          ) : undefined
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        <div className="flex min-w-0 flex-col gap-6">
          {isExternal ? <KbSourceCard kbId={kb.id} externalRef={kb.external_ref} /> : <KbDocuments ref={documentsRef} kbId={kb.id} />}
          <KbSearchPanel kbId={kb.id} />
          <KbEvalsCard kbId={kb.id} />
        </div>
        <aside className="flex min-w-0 flex-col gap-6" aria-label="About this knowledge base">
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Details</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <DescriptionList
                items={
                  isExternal
                    ? [
                        { term: "Documents", detail: pluralize(kb.document_count, "document", "documents"), mono: true },
                        { term: "Partition", detail: kb.external_ref ?? "—", mono: true },
                        { term: "Stored in", detail: storedIn },
                      ]
                    : [
                        { term: "Embedder", detail: embedderLabel(kb.embedder_id) },
                        { term: "Documents", detail: pluralize(kb.document_count, "document", "documents"), mono: true },
                        { term: "Chunks", detail: pluralize(kb.chunk_count, "chunk", "chunks"), mono: true },
                        { term: "Stored in", detail: storedIn },
                      ]
                }
              />
            </CardContent>
          </Card>
          {gate.can ? <KbDangerZone kb={kb} /> : null}
        </aside>
      </div>
    </Page>
  );
}

/** Kept apart so the router is only read where a delete can actually happen. */
function KbDangerZone({ kb }: { kb: KbOut }) {
  const router = useRouter();
  const deleteKb = useDeleteKb();
  const isExternal = kb.kind === "external";
  return (
    <DangerZoneCard
      actionLabel="Delete knowledge base"
      description="Deleting removes it from every agent that uses it. This can't be undone."
      confirmTitle={`Delete “${kb.name}”?`}
      confirmDescription="This permanently deletes the knowledge base. Agents that use it stop finding its answers."
      onConfirm={async () => {
        await deleteKb.mutateAsync(kb.id);
        toast.success(`${kb.name} deleted.`);
        router.push("/console/knowledge");
      }}
    >
      <ul className="list-disc pl-5">
        <li className="tabular-nums">
          {isExternal
            ? "The link to its Ragie partition (the documents stay in Ragie)"
            : `${pluralize(kb.document_count, "document", "documents")} and ${pluralize(kb.chunk_count, "chunk", "chunks")}`}
        </li>
      </ul>
    </DangerZoneCard>
  );
}

/** Mirrors the page: header, the main column's cards and the side column's facts. */
function KbDetailSkeleton() {
  return (
    <Page width="wide">
      <LoadingRegion label="Loading knowledge base">
        <div className="mb-6 flex flex-col gap-2">
          <Skeleton className="h-3.5 w-36" />
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-4 w-96 max-w-full" />
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex flex-col gap-6">
            <Skeleton className="h-72 w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
          </div>
          <Skeleton className="h-56 w-full rounded-lg" />
        </div>
      </LoadingRegion>
    </Page>
  );
}
