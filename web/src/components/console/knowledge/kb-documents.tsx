"use client";

import * as React from "react";
import { toast } from "sonner";
import { AlertCircleIcon, Loader2Icon, RotateCcwIcon, Trash2Icon, UploadCloudIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeleteKbDocument, useKbDocuments, useUploadKbDocument } from "@/components/console/lib/api-hooks";
import { isFileOverUploadCap, kbUploadCapErrorMessage } from "@/components/console/lib/upload";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { KbDocumentOut } from "@/contracts/lkap-contracts";

const ACCEPTED_EXTENSIONS = [".md", ".txt", ".pdf"];
const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(",");

interface QueueItem {
  id: string;
  name: string;
  status: "uploading" | "error";
  message?: string;
}

let queueSeq = 0;
function nextQueueId() {
  queueSeq += 1;
  return `queue-${queueSeq}`;
}

/**
 * `kb-documents.tsx` (docs/UI_UX_SPEC.md §7.7 item 2): a drag-and-drop zone
 * plus the document table. Per-file network progress would need
 * `upload.ts`'s `uploadKbDocument` to report XHR progress *through* WP-0's
 * `useUploadKbDocument` mutation (owned by `api-hooks.ts`, out of scope for
 * WP-6) — so, per the spec's own fallback ("or keeps fetch with an
 * indeterminate bar"), each queued file shows a spinner while its upload is
 * in flight instead of a percentage.
 */
export function KbDocuments({ kbId }: { kbId: string }) {
  const { data, isLoading, isError, error, refetch } = useKbDocuments(kbId, { pollWhilePending: true });
  const upload = useUploadKbDocument(kbId);
  const deleteDoc = useDeleteKbDocument(kbId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);
  const [queue, setQueue] = React.useState<QueueItem[]>([]);
  /** Set by "Try again" so the next file picked replaces that failed document. */
  const retryDocIdRef = React.useRef<string | null>(null);
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  function dismissQueueItem(id: string) {
    setQueue((items) => items.filter((item) => item.id !== id));
  }

  async function uploadOne(file: File) {
    if (isFileOverUploadCap(file)) {
      const message = kbUploadCapErrorMessage(file.name);
      setQueue((items) => [...items, { id: nextQueueId(), name: file.name, status: "error", message }]);
      toast.error(message);
      return;
    }

    const queueId = nextQueueId();
    setQueue((items) => [...items, { id: queueId, name: file.name, status: "uploading" }]);

    try {
      await upload.mutateAsync(file);
      dismissQueueItem(queueId);
      toast.success(`Uploaded ${file.name}.`);
      const replaces = retryDocIdRef.current;
      if (replaces) {
        retryDocIdRef.current = null;
        await deleteDoc.mutateAsync(replaces).catch(() => undefined);
      }
    } catch (err) {
      const message = errorMessage(err);
      setQueue((items) => items.map((item) => (item.id === queueId ? { ...item, status: "error", message } : item)));
      toast.error(`${file.name}: ${message}`);
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    await Promise.all(Array.from(files).map((file) => uploadOne(file)));
  }

  function openFilePicker(retryDocId?: string) {
    if (!canWrite) return;
    retryDocIdRef.current = retryDocId ?? null;
    fileInputRef.current?.click();
  }

  const columns: ResponsiveTableColumn<KbDocumentOut>[] = [
    {
      id: "file",
      header: "File",
      cell: (doc) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{doc.filename}</div>
          {doc.status === "failed" && doc.error ? (
            <div className="text-xs text-danger-text">{doc.error}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (doc) => <DocumentStatusChip status={doc.status} />,
    },
    {
      id: "chunks",
      header: "Chunks",
      cell: (doc) => <span className="tabular-nums">{doc.chunk_count}</span>,
    },
    {
      id: "size",
      header: "Size",
      cell: (doc) => <span className="text-muted-foreground">{formatBytes(doc.bytes)}</span>,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (doc) => (
        <div className="flex items-center justify-end gap-1">
          {doc.status === "failed" ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
              onClick={() => openFilePicker(doc.id)}
            >
              <Icon as={RotateCcwIcon} size="sm" /> Try again
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${doc.filename}`}
            disabled={!canWrite || deleteDoc.isPending}
            title={canWrite ? undefined : writeReason}
            onClick={() => {
              deleteDoc.mutate(doc.id, {
                onSuccess: () => toast.success(`${doc.filename} deleted.`),
                onError: (err) => toast.error(errorMessage(err)),
              });
            }}
          >
            <Icon as={Trash2Icon} size="sm" />
          </Button>
        </div>
      ),
    },
  ];

  const documents = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">Documents</h2>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTR}
        className="hidden"
        onChange={(event) => {
          void handleFiles(event.target.files);
          event.target.value = "";
        }}
      />

      {/* Drop zone: docs/UI_UX_SPEC.md §4.7 "Drop .md, .txt or .pdf files here, or browse". */}
      <div
        role="button"
        tabIndex={canWrite ? 0 : -1}
        aria-disabled={!canWrite}
        title={canWrite ? undefined : writeReason}
        aria-label={
          canWrite
            ? "Upload documents: drop files here or press Enter to browse"
            : `Upload documents (disabled): ${writeReason}`
        }
        onClick={() => openFilePicker()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openFilePicker();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (canWrite) setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setIsDragging(false);
          if (!canWrite) return;
          void handleFiles(event.dataTransfer.files);
        }}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-8 text-center transition-colors",
          canWrite ? "cursor-pointer" : "cursor-not-allowed opacity-50",
          isDragging ? "border-primary bg-muted/50" : canWrite ? "hover:bg-muted/30" : "",
        )}
      >
        <Icon as={UploadCloudIcon} size="lg" className="text-muted-foreground" />
        <p className="text-sm text-foreground">
          Drop <span className="font-mono text-xs">.md</span>, <span className="font-mono text-xs">.txt</span> or{" "}
          <span className="font-mono text-xs">.pdf</span> files here, or browse
        </p>
        <p className="text-xs text-muted-foreground">
          {canWrite ? "Up to 25 MB per file." : writeReason}
        </p>
      </div>

      {queue.length > 0 ? (
        <ul className="flex flex-col gap-1.5" aria-label="Uploads in progress">
          {queue.map((item) => (
            <li
              key={item.id}
              className={cn(
                "flex items-center gap-2 rounded-md border px-3 py-2 text-sm",
                item.status === "error" ? "border-danger-text/20 bg-danger-soft" : "border-border bg-muted/30",
              )}
            >
              {item.status === "uploading" ? (
                <Icon as={Loader2Icon} size="sm" className="animate-spin text-muted-foreground" />
              ) : (
                <Icon as={AlertCircleIcon} size="sm" className="text-danger-text" />
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate">{item.name}</div>
                {item.message ? <div className="text-xs text-danger-text">{item.message}</div> : null}
              </div>
              {item.status === "error" ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Dismiss ${item.name}`}
                  onClick={() => dismissQueueItem(item.id)}
                >
                  <Icon as={XIcon} size="sm" />
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : isError ? (
        <ErrorBanner message={errorMessage(error)} onRetry={() => refetch()} />
      ) : (
        <ResponsiveTable
          columns={columns}
          rows={documents}
          label="Documents"
          getRowKey={(doc) => doc.id}
          renderCard={(doc) => (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{doc.filename}</div>
                  {doc.status === "failed" && doc.error ? (
                    <div className="text-xs text-danger-text">{doc.error}</div>
                  ) : null}
                </div>
                <DocumentStatusChip status={doc.status} />
              </div>
              <div className="text-xs text-muted-foreground">
                {doc.chunk_count} chunks · {formatBytes(doc.bytes)}
              </div>
              <div className="flex items-center gap-2">
                {doc.status === "failed" ? (
                  <Button type="button" variant="outline" size="sm" onClick={() => openFilePicker(doc.id)}>
                    <Icon as={RotateCcwIcon} size="sm" /> Try again
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={deleteDoc.isPending}
                  onClick={() => {
                    deleteDoc.mutate(doc.id, {
                      onSuccess: () => toast.success(`${doc.filename} deleted.`),
                      onError: (err) => toast.error(errorMessage(err)),
                    });
                  }}
                >
                  <Icon as={Trash2Icon} size="sm" /> Delete
                </Button>
              </div>
            </div>
          )}
          empty={
            <EmptyState
              icon={UploadCloudIcon}
              title="No documents yet"
              description="Upload .md, .txt or .pdf files to make them searchable."
              compact
            />
          }
        />
      )}
    </div>
  );
}

function DocumentStatusChip({ status }: { status: KbDocumentOut["status"] }) {
  if (status === "pending") {
    return (
      <StatusChip tone="info">
        <Icon as={Loader2Icon} size="sm" className="animate-spin" /> Indexing…
      </StatusChip>
    );
  }
  if (status === "failed") {
    return <StatusChip tone="danger">Failed</StatusChip>;
  }
  return <StatusChip tone="success">Ready</StatusChip>;
}
