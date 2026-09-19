"use client";

import * as React from "react";
import Link from "next/link";
import { Controller, useFormContext } from "react-hook-form";
import { ExternalLinkIcon } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useKbs } from "@/components/console/lib/api-hooks";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

/**
 * "Knowledge (attach KBs; KB pages under `/console/knowledge` with upload +
 * status polling + test search)" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE).
 * Uploading/searching documents happens on the dedicated `/console/knowledge`
 * pages; this tab only attaches existing knowledge bases to the agent.
 */
export function KnowledgeTab() {
  const topKId = React.useId();
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const kbIds = watch("config.knowledge.kb_ids");
  const kbsQuery = useKbs();

  function toggle(id: string, attached: boolean) {
    const current = kbIds ?? [];
    setValue(
      "config.knowledge.kb_ids",
      attached ? Array.from(new Set([...current, id])) : current.filter((existing) => existing !== id),
      { shouldDirty: true },
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Attached knowledge bases</h3>
          <Link href="/console/knowledge" className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline">
            Manage knowledge bases <ExternalLinkIcon className="size-3" />
          </Link>
        </div>

        {kbsQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : kbsQuery.isError ? (
          <ErrorBanner message={errorMessage(kbsQuery.error)} onRetry={() => kbsQuery.refetch()} />
        ) : (kbsQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="No knowledge bases yet"
            description="Create one under Knowledge, upload documents, then attach it here."
          />
        ) : (
          <div className="space-y-2">
            {kbsQuery.data?.items.map((kb) => (
              <div key={kb.id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{kb.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {kb.chunk_count} chunks · {kb.document_count} documents · {kb.embedder_id}
                  </p>
                </div>
                <Switch checked={(kbIds ?? []).includes(kb.id)} onCheckedChange={(checked) => toggle(kb.id, checked)} />
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-4 rounded-xl border border-border bg-card p-4 sm:grid-cols-2">
        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
          <span className="text-sm font-medium">Auto-inject on each turn</span>
          <Controller
            control={control}
            name="config.knowledge.auto_inject"
            render={({ field }) => <Switch checked={field.value} onCheckedChange={field.onChange} />}
          />
        </div>
        <div>
          <label htmlFor={topKId} className="mb-1 block text-sm font-medium">
            Top K
          </label>
          <Controller
            control={control}
            name="config.knowledge.top_k"
            render={({ field }) => (
              <Input
                id={topKId}
                type="number"
                className="w-24"
                value={field.value}
                onChange={(event) => field.onChange(Number(event.target.value))}
              />
            )}
          />
        </div>
      </div>
    </div>
  );
}
