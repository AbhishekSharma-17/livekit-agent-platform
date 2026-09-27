"use client";

import * as React from "react";
import { DatabaseIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { SkeletonRows } from "@/components/shared/loading-state";
import { RequireWrite } from "@/components/shared/require-write";
import {
  useDeleteKnowledgeConnection,
  useKnowledgeConnections,
  useProviders,
  useTestKnowledgeConnection,
} from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import {
  KNOWLEDGE_CONNECTION_PROVIDER_ID,
  KnowledgeConnectionDialog,
  KnowledgeConnectionTestResultView,
  knowledgeConnectionKindLabel,
  knowledgeConnectionStatusMeta,
  MANAGED_SEARCH_CONNECTION_KINDS,
  RERANKER_CONNECTION_KINDS,
} from "./knowledge-connection-dialog";
import type { KnowledgeConnectionOut, KnowledgeConnectionTestOut } from "@/contracts/lkap-contracts";

/**
 * Settings → **Knowledge connections** (V5-24, K §5.3): the workspace's Qdrant
 * / Pinecone / Weaviate vector stores, Ragie managed search (V5-45) and
 * Cohere / Voyage AI hosted re-rankers — add, test, edit and delete. A
 * knowledge base picks one of the vector-store or Ragie rows when it is
 * created (`create-kb-dialog.tsx`); the agent's Knowledge tab picks a
 * re-ranker row for the search tool (`agents/tabs/knowledge-tab.tsx`).
 *
 * Reads need `builder`; every write (add, edit, delete, test) needs `admin`,
 * like the vault keys they use (`knowledge_connections/router.py`).
 */
export function KnowledgeConnectionsTab() {
  return (
    <RequireWrite min="builder" title="Only builders, admins and owners can see knowledge connections">
      <KnowledgeConnectionsTabInner />
    </RequireWrite>
  );
}

function KnowledgeConnectionsTabInner() {
  const query = useKnowledgeConnections();
  const providersQuery = useProviders();
  const providers = providersQuery.data?.providers ?? [];
  const deleteMutation = useDeleteKnowledgeConnection();
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  const [adding, setAdding] = React.useState(false);
  const [editing, setEditing] = React.useState<KnowledgeConnectionOut | null>(null);
  const [testing, setTesting] = React.useState<KnowledgeConnectionOut | null>(null);

  const connections = query.data?.items ?? [];

  async function onDelete(connection: KnowledgeConnectionOut) {
    try {
      await deleteMutation.mutateAsync(connection.id);
      toast.success(`Deleted "${connection.name}".`);
    } catch (error) {
      toast.error(`Couldn't delete "${connection.name}" — ${errorMessage(error)}`);
    }
  }

  const columns: ResponsiveTableColumn<KnowledgeConnectionOut>[] = [
    {
      id: "name",
      header: "Connection",
      cell: (connection) => {
        const spec = providers.find((p) => p.id === KNOWLEDGE_CONNECTION_PROVIDER_ID[connection.kind]);
        return (
          <div className="flex min-w-0 items-center gap-2.5">
            <VendorMark vendor={spec?.vendor ?? connection.kind} size="sm" />
            <div className="min-w-0">
              <div className="truncate font-medium text-foreground">{connection.name}</div>
              <div className="truncate text-xs text-muted-foreground">
                {knowledgeConnectionKindLabel(connection.kind, providers)}
                {RERANKER_CONNECTION_KINDS.includes(connection.kind)
                  ? " · re-ranker"
                  : MANAGED_SEARCH_CONNECTION_KINDS.includes(connection.kind)
                    ? " · managed search"
                    : ""}
              </div>
            </div>
          </div>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (connection) => {
        const meta = knowledgeConnectionStatusMeta(connection.status);
        return (
          <span title={connection.last_error ?? undefined}>
            <StatusChip tone={meta.tone} size="sm">
              {meta.label}
            </StatusChip>
          </span>
        );
      },
    },
    {
      id: "key",
      header: "Key",
      cell: (connection) =>
        connection.credential_fingerprint ? (
          <span className="font-mono text-xs text-muted-foreground">{connection.credential_fingerprint}</span>
        ) : (
          <span className="text-xs text-muted-foreground">No key</span>
        ),
    },
    {
      id: "kbs",
      header: "Knowledge bases",
      cell: (connection) => (
        <span className="tabular-nums text-muted-foreground">{connection.knowledge_base_count}</span>
      ),
    },
    {
      id: "checked",
      header: "Last checked",
      cell: (connection) =>
        connection.last_checked_at ? (
          <RelativeTime iso={connection.last_checked_at} />
        ) : (
          <span className="text-muted-foreground">Never</span>
        ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (connection) => (
        <RowActions connection={connection} canWrite={canWrite} writeReason={writeReason} onTest={setTesting} onEdit={setEditing} onDelete={onDelete} />
      ),
    },
  ];

  return (
    <Section
      id="knowledge-connections"
      title="Knowledge connections"
      description="Keep a knowledge base's vectors in your own Qdrant, Pinecone or Weaviate account, search documents kept in Ragie, or re-rank search results with a hosted service."
      aside={
        <Button type="button" size="sm" onClick={() => canWrite && setAdding(true)} disabled={!canWrite} title={canWrite ? undefined : writeReason}>
          <PlusIcon />
          Add connection
        </Button>
      }
    >
      <SectionRow>
        {query.isLoading ? (
          <SkeletonRows label="Loading knowledge connections" rowClassName="h-12" />
        ) : query.isError ? (
          <ErrorBanner message={`Couldn't load knowledge connections — ${errorMessage(query.error)}`} onRetry={() => query.refetch()} />
        ) : connections.length === 0 ? (
          <EmptyState
            icon={DatabaseIcon}
            title="No knowledge connections yet"
            description="Every knowledge base keeps its vectors on this platform until you add one."
            compact
          />
        ) : (
          <ResponsiveTable<KnowledgeConnectionOut>
            columns={columns}
            rows={connections}
            label="Knowledge connections"
            getRowKey={(connection) => connection.id}
            renderCard={(connection) => {
              const meta = knowledgeConnectionStatusMeta(connection.status);
              return (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-foreground">{connection.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {knowledgeConnectionKindLabel(connection.kind, providers)}
                      </div>
                    </div>
                    <StatusChip tone={meta.tone} size="sm">
                      {meta.label}
                    </StatusChip>
                  </div>
                  <RowActions
                    connection={connection}
                    canWrite={canWrite}
                    writeReason={writeReason}
                    onTest={setTesting}
                    onEdit={setEditing}
                    onDelete={onDelete}
                  />
                </div>
              );
            }}
          />
        )}
      </SectionRow>

      <KnowledgeConnectionDialog open={adding} onOpenChange={setAdding} />
      {editing ? (
        <KnowledgeConnectionDialog open onOpenChange={(next) => !next && setEditing(null)} connection={editing} />
      ) : null}
      {testing ? <TestConnectionDialog connection={testing} onClose={() => setTesting(null)} /> : null}
    </Section>
  );
}

/**
 * Test / Edit / Delete, shared by the desktop table's actions column and the
 * mobile card (both are in the DOM — `ResponsiveTable` switches by CSS — so
 * the phone-width view stays actionable, not read-only). `Test` needs
 * `admin` server-side too (`knowledge_connections/router.py::test_connection`
 * uses the same `WriteCtx` as create/update/delete), so it is gated exactly
 * like the other two rather than left open to a builder who would only get
 * a 403.
 */
function RowActions({
  connection,
  canWrite,
  writeReason,
  onTest,
  onEdit,
  onDelete,
}: {
  connection: KnowledgeConnectionOut;
  canWrite: boolean;
  writeReason: string;
  onTest: (connection: KnowledgeConnectionOut) => void;
  onEdit: (connection: KnowledgeConnectionOut) => void;
  onDelete: (connection: KnowledgeConnectionOut) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => canWrite && onTest(connection)}
        disabled={!canWrite}
        title={canWrite ? undefined : writeReason}
      >
        Test
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => canWrite && onEdit(connection)}
        disabled={!canWrite}
        title={canWrite ? undefined : writeReason}
      >
        Edit
      </Button>
      <ConfirmDialog
        trigger={
          <Button type="button" variant="ghost" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
            Delete
          </Button>
        }
        title={`Delete "${connection.name}"?`}
        description={
          (connection.knowledge_base_count ?? 0) > 0
            ? `${connection.knowledge_base_count} knowledge base(s) still store their vectors here — delete those first, or move them.`
            : "The data stays in your own account; only the connection is removed here."
        }
        confirmLabel="Delete connection"
        onConfirm={() => onDelete(connection)}
      />
    </div>
  );
}

/** "Test connection" from the table row: runs the test and shows the result in place (no side drawer). */
function TestConnectionDialog({ connection, onClose }: { connection: KnowledgeConnectionOut; onClose: () => void }) {
  const testMutation = useTestKnowledgeConnection();
  const [result, setResult] = React.useState<KnowledgeConnectionTestOut | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const ranRef = React.useRef(false);

  React.useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    testMutation
      .mutateAsync(connection.id)
      .then(setResult)
      .catch((err: unknown) => setError(errorMessage(err)));
    // Runs once per mount (one dialog instance per test).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Test &quot;{connection.name}&quot;</DialogTitle>
        </DialogHeader>
        <DialogBody>
          {testMutation.isPending ? (
            <p className="text-sm text-muted-foreground">Testing…</p>
          ) : error ? (
            <ErrorBanner message={error} />
          ) : (
            <KnowledgeConnectionTestResultView result={result} kind={connection.kind} />
          )}
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}
