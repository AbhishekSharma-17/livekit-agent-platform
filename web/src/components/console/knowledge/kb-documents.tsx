"use client";

import * as React from "react";
import { toast } from "sonner";
import { Trash2Icon, UploadIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDeleteKbDocument, useKbDocuments, useUploadKbDocument } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { KbDocumentOut } from "@/contracts/lkap-contracts";

const STATUS_VARIANT: Record<KbDocumentOut["status"], "default" | "secondary" | "destructive"> = {
  pending: "secondary",
  ready: "default",
  failed: "destructive",
};

export function KbDocuments({ kbId }: { kbId: string }) {
  const { data, isLoading, isError, error, refetch } = useKbDocuments(kbId, { pollWhilePending: true });
  const upload = useUploadKbDocument(kbId);
  const deleteDoc = useDeleteKbDocument(kbId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      try {
        await upload.mutateAsync(file);
        toast.success(`Uploaded ${file.name}.`);
      } catch (error) {
        toast.error(`${file.name}: ${errorMessage(error)}`);
      }
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Documents</h3>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.pdf"
            className="hidden"
            onChange={(event) => {
              void handleFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <Button type="button" variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={upload.isPending}>
            <UploadIcon className="size-3.5" /> {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : isError ? (
        <ErrorBanner message={errorMessage(error)} onRetry={() => refetch()} />
      ) : (data?.items.length ?? 0) === 0 ? (
        <EmptyState title="No documents yet" description="Upload .txt, .md or .pdf files to make them searchable." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Chunks</TableHead>
              <TableHead>Size</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell className="font-medium">
                  {doc.filename}
                  {doc.error ? <div className="text-xs text-destructive">{doc.error}</div> : null}
                </TableCell>
                <TableCell>
                  <Badge variant={STATUS_VARIANT[doc.status]}>{doc.status}</Badge>
                </TableCell>
                <TableCell>{doc.chunk_count}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{(doc.bytes / 1024).toFixed(1)} KB</TableCell>
                <TableCell className="text-right">
                  <ConfirmDialog
                    trigger={
                      <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${doc.filename}`}>
                        <Trash2Icon className="size-3.5" />
                      </Button>
                    }
                    title={`Delete "${doc.filename}"?`}
                    confirmLabel="Delete"
                    onConfirm={async () => {
                      try {
                        await deleteDoc.mutateAsync(doc.id);
                        toast.success(`${doc.filename} deleted.`);
                      } catch (error) {
                        toast.error(errorMessage(error));
                      }
                    }}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
