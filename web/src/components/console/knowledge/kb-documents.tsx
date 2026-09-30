"use client";

import * as React from "react";
import { toast } from "sonner";
import { CircleAlertIcon, RefreshCwIcon, Trash2Icon, UploadIcon, XIcon } from "lucide-react";

import { Button, IconButton } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useDeleteKbDocument, useKbDocuments, useUploadKbDocument } from "@/components/console/lib/api-hooks";
import { isFileOverUploadCap, kbUploadCapErrorMessage } from "@/components/console/lib/upload";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { plainStatusError } from "@/components/shared/status-error";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { Icon } from "@/components/shared/icon";
import { LoadingRegion } from "@/components/shared/loading-state";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { LifecycleBadge } from "@/components/shared/status-chip";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { KbDocumentOut } from "@/contracts/lkap-contracts";

const ACCEPTED_EXTENSIONS = [".md", ".txt", ".pdf"];
const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(",");
const FAILED_FALLBACK = "This file couldn't be read. Try uploading it again.";

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

/** A plain file-type label from the stored mime type (falls back to the extension, then "File"). */
function fileTypeLabel(doc: Pick<KbDocumentOut, "mime" | "filename">): string {
  if (doc.mime === "application/pdf") return "PDF";
  if (doc.mime === "text/markdown" || doc.mime === "text/x-markdown") return "Markdown";
  if (doc.mime === "text/plain") return "Text";
  const extension = doc.filename.split(".").pop()?.toLowerCase();
  if (extension === "pdf") return "PDF";
  if (extension === "md" || extension === "markdown") return "Markdown";
  if (extension === "txt") return "Text";
  return "File";
}

/** Lets the page header's "Upload documents" open this card's file picker. */
export interface KbDocumentsHandle {
  openFilePicker: () => void;
}

/**
 * `kb-documents.tsx` (docs/UI_UX_SPEC.md §7.7 item 2): a drag-and-drop zone
 * plus the document table, in one card. Per-file network progress would need
 * `upload.ts`'s `uploadKbDocument` to report XHR progress *through* WP-0's
 * `useUploadKbDocument` mutation (owned by `api-hooks.ts`, out of scope for
 * WP-6) — so, per the spec's own fallback ("or keeps fetch with an
 * indeterminate bar"), each queued file shows a spinner while its upload is
 * in flight instead of a percentage. Viewers see the documents without the
 * drop zone, retry or delete (decision D12); deleting asks first.
 */
export const KbDocuments = React.forwardRef<KbDocumentsHandle, { kbId: string }>(function KbDocuments({ kbId }, ref) {
  const { data, isLoading, isError, error, refetch } = useKbDocuments(kbId, { pollWhilePending: true });
  const upload = useUploadKbDocument(kbId);
  const deleteDoc = useDeleteKbDocument(kbId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);
  const [queue, setQueue] = React.useState<QueueItem[]>([]);
  /** Set by "Try again" so the next file picked replaces that failed document. */
  const retryDocIdRef = React.useRef<string | null>(null);
  const gate = useWriteGate();

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
      toast.error(`Couldn't upload ${file.name}`, { description: message });
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    await Promise.all(Array.from(files).map((file) => uploadOne(file)));
  }

  function openFilePicker(retryDocId?: string) {
    if (!gate.can) return;
    retryDocIdRef.current = retryDocId ?? null;
    fileInputRef.current?.click();
  }

  React.useImperativeHandle(ref, () => ({ openFilePicker: () => openFilePicker() }));

  function deleteDocument(doc: KbDocumentOut) {
    return new Promise<void>((resolve, reject) => {
      deleteDoc.mutate(doc.id, {
        onSuccess: () => {
          toast.success(`${doc.filename} deleted.`);
          resolve();
        },
        onError: (err) => reject(err),
      });
    });
  }

  const failedLine = (doc: KbDocumentOut) =>
    doc.status === "failed" ? (
      <div className="text-caption text-destructive-text">{plainStatusError(doc.error, FAILED_FALLBACK)}</div>
    ) : null;

  const rowActions = (doc: KbDocumentOut, layout: "table" | "card") =>
    gate.show ? (
      <div className={cn("flex items-center gap-1", layout === "table" && "justify-end")}>
        {doc.status === "failed" ? (
          <Button type="button" variant="secondary" size="sm" disabled={gate.pending} onClick={() => openFilePicker(doc.id)}>
            <RefreshCwIcon aria-hidden="true" /> Try again
          </Button>
        ) : null}
        <ConfirmDialog
          trigger={
            layout === "table" ? (
              <IconButton label={`Delete ${doc.filename}`} size="sm" disabled={gate.pending || deleteDoc.isPending}>
                <Trash2Icon />
              </IconButton>
            ) : (
              <Button type="button" variant="ghost" size="sm" disabled={gate.pending || deleteDoc.isPending}>
                <Trash2Icon aria-hidden="true" /> Delete
              </Button>
            )
          }
          title={`Delete “${doc.filename}”?`}
          description="Agents stop finding its passages straight away. Upload it again to bring it back."
          confirmLabel="Delete document"
          onConfirm={() => deleteDocument(doc)}
        />
      </div>
    ) : null;

  const columns: ResponsiveTableColumn<KbDocumentOut>[] = [
    {
      id: "file",
      header: "File",
      cell: (doc) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{doc.filename}</div>
          {failedLine(doc)}
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (doc) => <DocumentStatus status={doc.status} progress={doc.progress} />,
    },
    {
      id: "type",
      header: "Type",
      cell: (doc) => <span className="text-text-secondary">{fileTypeLabel(doc)}</span>,
    },
    {
      id: "chunks",
      header: "Chunks",
      align: "end",
      cell: (doc) => <span className="tabular-nums">{doc.chunk_count}</span>,
    },
    {
      id: "size",
      header: "Size",
      align: "end",
      cell: (doc) => <span className="text-text-secondary tabular-nums">{formatBytes(doc.bytes)}</span>,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (doc) => rowActions(doc, "table"),
    },
  ];

  const documents = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Documents</h2>
        </CardTitle>
        <CardDescription>Markdown, text and PDF files, chunked and embedded so agents can search them.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
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
        {gate.pending ? (
          // Until the role is known the zone is a placeholder, never a live control.
          <Skeleton aria-hidden="true" className="h-[154px] w-full rounded-lg" />
        ) : gate.show ? (
          <div
            role="button"
            tabIndex={0}
            aria-label="Upload documents: drop files here or press Enter to browse"
            onClick={() => openFilePicker()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openFilePicker();
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              if (gate.can) setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              if (!gate.can) return;
              void handleFiles(event.dataTransfer.files);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-8 text-center transition-colors duration-(--duration-fast)",
              isDragging ? "border-brand bg-brand-subtle" : "hover:bg-muted",
            )}
          >
            <span className="flex size-10 items-center justify-center rounded bg-muted text-text-secondary">
              <Icon as={UploadIcon} size="tile" />
            </span>
            <p className="text-body text-foreground">
              Drop <span className="font-mono text-caption">.md</span>, <span className="font-mono text-caption">.txt</span> or{" "}
              <span className="font-mono text-caption">.pdf</span> files here, or browse
            </p>
            <p className="text-caption text-text-secondary">Up to 25 MB per file.</p>
          </div>
        ) : (
          <ReadOnlyNote variant="block">{readOnlyCopy("builder", "upload or remove documents")}</ReadOnlyNote>
        )}

        {queue.length > 0 ? (
          <ul className="flex flex-col gap-1.5" aria-label="Uploads in progress">
            {queue.map((item) => (
              <li
                key={item.id}
                className={cn(
                  "flex items-center gap-2 rounded border px-3 py-2 text-label",
                  item.status === "error" ? "border-destructive-border bg-destructive-subtle" : "border-border bg-muted",
                )}
              >
                {item.status === "uploading" ? (
                  <Spinner aria-hidden="true" />
                ) : (
                  <Icon as={CircleAlertIcon} size="sm" className="text-destructive-text" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate">
                    {item.name}
                    {item.status === "uploading" ? <span className="text-text-secondary"> — uploading…</span> : null}
                  </div>
                  {item.message ? <div className="text-caption text-destructive-text">{item.message}</div> : null}
                </div>
                {item.status === "error" ? (
                  <IconButton label={`Dismiss ${item.name}`} size="sm" onClick={() => dismissQueueItem(item.id)}>
                    <XIcon />
                  </IconButton>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}

        {isLoading ? (
          <LoadingRegion label="Loading documents" className="flex flex-col gap-2">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} className="h-12 w-full" />
            ))}
          </LoadingRegion>
        ) : isError ? (
          <ErrorBanner error={error} context={{ action: "load documents" }} onRetry={() => refetch()} />
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
                    {failedLine(doc)}
                  </div>
                  <DocumentStatus status={doc.status} progress={doc.progress} />
                </div>
                <div className="text-caption text-text-secondary tabular-nums">
                  {fileTypeLabel(doc)} · {doc.chunk_count} chunks · {formatBytes(doc.bytes)}
                </div>
                {rowActions(doc, "card")}
              </div>
            )}
            empty={
              <EmptyState
                icon={UploadIcon}
                title="No documents yet"
                description={
                  gate.show ? "Upload .md, .txt or .pdf files to make them searchable." : "Nothing has been uploaded here yet."
                }
                compact
              />
            }
          />
        )}
      </CardContent>
    </Card>
  );
});

function DocumentStatus({ status, progress }: { status: KbDocumentOut["status"]; progress?: number | null }) {
  if (status === "pending") {
    const pct = typeof progress === "number" ? Math.round(Math.max(0, Math.min(1, progress)) * 100) : null;
    return (
      <div className="flex items-center gap-2">
        <LifecycleBadge state="indexing" label={`Indexing${pct !== null ? ` ${pct}%` : "…"}`} />
        {pct !== null ? <Progress value={pct} className="w-16" aria-hidden="true" /> : null}
      </div>
    );
  }
  if (status === "failed") return <LifecycleBadge state="failed" />;
  return <LifecycleBadge state="ready" />;
}
