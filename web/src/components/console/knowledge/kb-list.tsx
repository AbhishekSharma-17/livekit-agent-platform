"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDeleteKb, useKbs } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";

export function KbList() {
  const { data, isLoading, isError, error, refetch } = useKbs();
  const deleteKb = useDeleteKb();

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (isError) return <ErrorBanner message={`Could not reach the api: ${errorMessage(error)}`} onRetry={() => refetch()} />;

  const kbs = data?.items ?? [];
  if (kbs.length === 0) {
    return (
      <EmptyState
        title="No knowledge bases yet"
        description="Create one, upload documents, then attach it to an agent."
        action={<CreateKbDialog />}
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Embedder</TableHead>
          <TableHead>Documents</TableHead>
          <TableHead>Chunks</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {kbs.map((kb) => (
          <TableRow key={kb.id}>
            <TableCell className="font-medium">
              <Link href={`/console/knowledge/${kb.id}`} className="hover:underline">
                {kb.name}
              </Link>
              {kb.description ? <div className="text-xs text-muted-foreground">{kb.description}</div> : null}
            </TableCell>
            <TableCell className="text-sm text-muted-foreground">{kb.embedder_id}</TableCell>
            <TableCell>{kb.document_count}</TableCell>
            <TableCell>{kb.chunk_count}</TableCell>
            <TableCell className="text-right">
              <div className="flex items-center justify-end gap-1">
                <Button asChild variant="outline" size="sm">
                  <Link href={`/console/knowledge/${kb.id}`}>Open</Link>
                </Button>
                <ConfirmDialog
                  trigger={
                    <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${kb.name}`}>
                      <Trash2Icon className="size-3.5" />
                    </Button>
                  }
                  title={`Delete "${kb.name}"?`}
                  description="This permanently deletes the knowledge base and its documents."
                  confirmLabel="Delete"
                  onConfirm={async () => {
                    try {
                      await deleteKb.mutateAsync(kb.id);
                      toast.success(`${kb.name} deleted.`);
                    } catch (error) {
                      toast.error(errorMessage(error));
                    }
                  }}
                />
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
