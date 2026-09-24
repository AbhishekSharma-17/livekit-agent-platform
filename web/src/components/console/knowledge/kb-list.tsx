"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { BookOpenIcon, MoreVerticalIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useDeleteKb, useKbs } from "@/components/console/lib/api-hooks";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { embedderLabel } from "@/components/console/knowledge/embedder-label";
import { useWriteAccess } from "@/components/console/lib/roles";
import { Icon } from "@/components/shared/icon";
import { pluralize } from "@/lib/format";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import type { KbOut } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";

/**
 * `kb-list.tsx` (docs/UI_UX_SPEC.md §7.7 item 1): `ResponsiveTable`; columns
 * Name (+ description), Documents, Chunks, Embedder (human label), Updated;
 * row link; actions menu; empty state. The old "Open" button duplicated the
 * row link (§1.5 M finding) — it's gone, the whole row is the link now.
 */
export function KbList() {
  const { data, isLoading, isError, error, refetch } = useKbs();

  if (isLoading) {
    return (
      <LoadingRegion label="Loading knowledge bases" className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </LoadingRegion>
    );
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't load knowledge bases — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const kbs = data?.items ?? [];

  const columns: ResponsiveTableColumn<KbOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (kb) => (
        <div className="min-w-0">
          <div className="font-medium text-foreground">{kb.name}</div>
          {kb.description ? (
            <div className="truncate text-xs text-muted-foreground">{kb.description}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "documents",
      header: "Documents",
      cell: (kb) => <span className="tabular-nums">{kb.document_count}</span>,
    },
    {
      id: "chunks",
      header: "Chunks",
      cell: (kb) => <span className="tabular-nums">{kb.chunk_count}</span>,
    },
    {
      id: "embedder",
      header: "Embedder",
      cell: (kb) => <span className="text-muted-foreground">{embedderLabel(kb.embedder_id)}</span>,
    },
    {
      id: "updated",
      header: "Updated",
      cell: (kb) => <RelativeTime iso={kb.updated_at} />,
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
    <ResponsiveTable
      columns={columns}
      rows={kbs}
      label="Knowledge bases"
      rowHref={(kb) => `/console/knowledge/${kb.id}`}
      getRowKey={(kb) => kb.id}
      renderCard={(kb) => (
        <div className="flex flex-col gap-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="font-medium text-foreground">{kb.name}</div>
              {kb.description ? <div className="text-xs text-muted-foreground">{kb.description}</div> : null}
            </div>
            <KbRowActions kb={kb} />
          </div>
          <div className="text-xs text-muted-foreground">
            {pluralize(kb.document_count, "document", "documents")} · {pluralize(kb.chunk_count, "chunk", "chunks")} ·{" "}
            {embedderLabel(kb.embedder_id)}
          </div>
        </div>
      )}
      empty={
        <EmptyState
          icon={BookOpenIcon}
          title="No knowledge bases yet"
          description="Upload documents the agent can search during a call."
          action={<CreateKbDialog />}
        />
      }
    />
  );
}

function KbRowActions({ kb }: { kb: KbOut }) {
  const deleteKb = useDeleteKb();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const { canWrite } = useWriteAccess();

  async function handleDelete() {
    try {
      await deleteKb.mutateAsync(kb.id);
      toast.success(`${kb.name} deleted.`);
      setConfirmOpen(false);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={`Actions for ${kb.name}`}>
            <Icon as={MoreVerticalIcon} size="md" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem asChild>
            <Link href={`/console/knowledge/${kb.id}`}>Open</Link>
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            disabled={!canWrite}
            onSelect={(event) => {
              event.preventDefault();
              setMenuOpen(false);
              setConfirmOpen(true);
            }}
          >
            <Icon as={Trash2Icon} size="md" /> Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &quot;{kb.name}&quot;?</DialogTitle>
            <DialogDescription>This permanently deletes the knowledge base and its documents.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
              disabled={deleteKb.isPending}
              onClick={() => void handleDelete()}
            >
              {deleteKb.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
