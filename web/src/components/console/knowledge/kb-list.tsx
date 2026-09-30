"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRightIcon, BookOpenIcon, Trash2Icon } from "lucide-react";

import { useDeleteKb, useKbs } from "@/components/console/lib/api-hooks";
import { TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import { Highlight, ListToolbar, SEARCH_THRESHOLD, matchesQuery } from "@/components/console/tools/list-search";
import { useWriteGate } from "@/components/console/tools/write-gate";
import { NoMatches } from "@/components/shared/empty-state";
import { SkeletonRows } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { RowMenu } from "@/components/shared/row-menu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Tag } from "@/components/shared/tag";
import { pluralize } from "@/lib/format";
import type { KbOut } from "@/contracts/lkap-contracts";

/**
 * `kb-list.tsx` (docs/UI_UX_SPEC.md §7.7 item 1): `ResponsiveTable`; columns
 * Name (+ description), Documents, Chunks, Embedder (human label), Updated;
 * the whole row links to the knowledge base; a "…" menu (Open, then Delete
 * last, behind a typed confirmation because it destroys the documents).
 * Search once there are six or more (docs/ui/DESIGN-SYSTEM.md section 9),
 * with separate "nothing yet" and "no matches" states.
 */
export function KbList() {
  const { data, isLoading, isError, error, refetch } = useKbs();
  const gate = useWriteGate();
  const [query, setQuery] = React.useState("");

  if (isLoading) {
    return <SkeletonRows label="Loading knowledge bases" rows={3} rowClassName="h-14" />;
  }

  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load knowledge bases" }} onRetry={() => refetch()} />;
  }

  const kbs = data?.items ?? [];
  const filtering = query.trim() !== "";
  const visible = kbs.filter((kb) =>
    matchesQuery([kb.name, kb.description, kb.kind === "external" ? "Managed search" : embedderLabel(kb.embedder_id)], query),
  );

  const nameBlock = (kb: KbOut) => (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-foreground">
          <Highlight text={kb.name} query={query} />
        </span>
        {kb.kind === "external" ? <Tag>Managed search</Tag> : null}
      </div>
      {kb.description ? (
        <div className="truncate text-caption text-text-secondary">
          <Highlight text={kb.description} query={query} />
        </div>
      ) : null}
    </div>
  );

  const columns: ResponsiveTableColumn<KbOut>[] = [
    { id: "name", header: "Name", cell: nameBlock },
    {
      id: "documents",
      header: "Documents",
      align: "end",
      cell: (kb) => <span className="tabular-nums">{kb.document_count}</span>,
    },
    {
      id: "chunks",
      header: "Chunks",
      align: "end",
      // No local vectors for a managed search (Ragie) knowledge base — nothing to count.
      cell: (kb) =>
        kb.kind === "external" ? <span className="text-text-tertiary">—</span> : <span className="tabular-nums">{kb.chunk_count}</span>,
    },
    {
      id: "embedder",
      header: "Embedder",
      cell: (kb) =>
        kb.kind === "external" ? (
          <span className="text-text-tertiary">—</span>
        ) : (
          <span className="text-text-secondary">{embedderLabel(kb.embedder_id)}</span>
        ),
    },
    {
      id: "updated",
      header: "Updated",
      cell: (kb) => <RelativeTime iso={kb.updated_at} className="text-text-secondary" />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (kb) => <KbRowActions kb={kb} />,
    },
  ];

  return (
    <div>
      {kbs.length >= SEARCH_THRESHOLD || filtering ? (
        <ListToolbar items="knowledge bases" query={query} onQueryChange={setQuery} />
      ) : null}
      <ResponsiveTable
        columns={columns}
        rows={visible}
        label="Knowledge bases"
        rowHref={(kb) => `/console/knowledge/${kb.id}`}
        getRowKey={(kb) => kb.id}
        renderCard={(kb) => (
          <div className="flex flex-col gap-1">
            <div className="flex items-start justify-between gap-2">
              {nameBlock(kb)}
              <KbRowActions kb={kb} />
            </div>
            <div className="text-caption text-text-secondary tabular-nums">
              {kb.kind === "external"
                ? pluralize(kb.document_count, "document", "documents")
                : `${pluralize(kb.document_count, "document", "documents")} · ${pluralize(kb.chunk_count, "chunk", "chunks")} · ${embedderLabel(kb.embedder_id)}`}
            </div>
          </div>
        )}
        empty={
          filtering ? (
            <NoMatches items="knowledge bases" query={query} onClear={() => setQuery("")} />
          ) : (
            <EmptyState
              icon={BookOpenIcon}
              title="No knowledge bases yet"
              description={
                gate.show ? "Upload documents the agent can search during a call." : "Nobody has added a knowledge base yet."
              }
              action={gate.show ? <CreateKbDialog variant="secondary" /> : undefined}
            />
          )
        }
      />
    </div>
  );
}

function KbRowActions({ kb }: { kb: KbOut }) {
  const deleteKb = useDeleteKb();
  const gate = useWriteGate();
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  async function handleDelete() {
    await deleteKb.mutateAsync(kb.id);
    toast.success(`${kb.name} deleted.`);
  }

  return (
    <>
      <RowMenu
        label={`Actions for ${kb.name}`}
        size="sm"
        destructive={gate.can ? { label: "Delete", icon: Trash2Icon, onSelect: () => setConfirmOpen(true) } : undefined}
      >
        <DropdownMenuItem asChild>
          <Link href={`/console/knowledge/${kb.id}`}>
            <ArrowRightIcon aria-hidden="true" /> Open
          </Link>
        </DropdownMenuItem>
      </RowMenu>
      <TypedConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Delete “${kb.name}”?`}
        description="This permanently deletes the knowledge base. Agents that use it stop finding its answers."
        confirmText="DELETE"
        confirmLabel="Delete knowledge base"
        onConfirm={handleDelete}
      >
        <ul className="list-disc pl-5">
          <li className="tabular-nums">
            {kb.kind === "external"
              ? "The link to its Ragie partition (the documents stay in Ragie)"
              : `${pluralize(kb.document_count, "document", "documents")} and ${pluralize(kb.chunk_count, "chunk", "chunks")}`}
          </li>
        </ul>
      </TypedConfirmDialog>
    </>
  );
}
