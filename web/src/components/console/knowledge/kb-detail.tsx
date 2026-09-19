"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { useKb } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { PageHeader } from "@/components/console/shared/page-header";
import { KbDocuments } from "@/components/console/knowledge/kb-documents";
import { KbSearchPanel } from "@/components/console/knowledge/kb-search-panel";

export function KbDetail({ kbId }: { kbId: string }) {
  const { data: kb, isLoading, isError, error, refetch } = useKb(kbId);

  return (
    <div>
      <Link href="/console/knowledge" className="mb-4 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeftIcon className="size-3.5" /> Knowledge
      </Link>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError || !kb ? (
        <ErrorBanner message={`Could not load this knowledge base: ${errorMessage(error)}`} onRetry={() => refetch()} />
      ) : (
        <>
          <PageHeader title={kb.name} description={kb.description || undefined} />
          <div className="space-y-4">
            <KbDocuments kbId={kb.id} />
            <KbSearchPanel kbId={kb.id} />
          </div>
        </>
      )}
    </div>
  );
}
