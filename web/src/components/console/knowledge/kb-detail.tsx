"use client";

import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useKb, useKnowledgeConnections, useProviders } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { PageHeader } from "@/components/console/shared/page-header";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import { knowledgeConnectionKindLabel } from "@/components/console/settings/knowledge-connection-dialog";
import { KbDocuments } from "@/components/console/knowledge/kb-documents";
import { KbEvalsCard } from "@/components/console/knowledge/kb-evals-card";
import { KbSearchPanel } from "@/components/console/knowledge/kb-search-panel";
import { DescriptionList } from "@/components/shared/description-list";
import { pluralize } from "@/lib/format";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { LoadingRegion } from "@/components/shared/loading-state";

export function KbDetail({ kbId }: { kbId: string }) {
  const { data: kb, isLoading, isError, error, refetch } = useKb(kbId);
  const connectionsQuery = useKnowledgeConnections();
  const providersQuery = useProviders();

  if (isLoading) {
    return (
      <LoadingRegion label="Loading knowledge base">
        <Skeleton className="mb-6 h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </LoadingRegion>
    );
  }

  if (isError || !kb) {
    return <ErrorBanner message={`Couldn't load this knowledge base — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const connection = kb.connection_id
    ? (connectionsQuery.data?.items ?? []).find((item) => item.id === kb.connection_id)
    : null;
  const storedIn = kb.connection_id
    ? connection
      ? `${connection.name} (${knowledgeConnectionKindLabel(connection.kind, providersQuery.data?.providers ?? [])})`
      : "A knowledge connection"
    : "This platform";

  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Knowledge", href: "/console/knowledge" }, { label: kb.name }]} />
      <PageHeader
        title={kb.name}
        description={kb.description || undefined}
      />

      <DescriptionList
        className="mb-6"
        columns={3}
        items={[
          { term: "Embedder", detail: embedderLabel(kb.embedder_id) },
          { term: "Documents", detail: pluralize(kb.document_count, "document", "documents"), mono: true },
          { term: "Chunks", detail: pluralize(kb.chunk_count, "chunk", "chunks"), mono: true },
          { term: "Stored in", detail: storedIn },
        ]}
      />

      <div className="flex flex-col gap-8">
        <KbDocuments kbId={kb.id} />
        <KbSearchPanel kbId={kb.id} />
        <KbEvalsCard kbId={kb.id} />
      </div>
    </div>
  );
}
