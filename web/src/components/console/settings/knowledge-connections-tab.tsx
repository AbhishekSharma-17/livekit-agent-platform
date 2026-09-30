"use client";

import * as React from "react";
import { PlugIcon, PlusIcon } from "lucide-react";
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
import { LoadingRow } from "@/components/shared/loading-state";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { IfCan, readOnlyCopy, useCan } from "@/components/console/shared/permission";
import { RequireWrite } from "@/components/shared/require-write";
import {
  useDeleteKnowledgeConnection,
  useKnowledgeConnections,
  useProviders,
  useTestKnowledgeConnection,
} from "@/components/console/lib/api-hooks";
import {
  KNOWLEDGE_CONNECTION_PROVIDER_ID,
  KnowledgeConnectionDialog,
  KnowledgeConnectionTestResultView,
  knowledgeConnectionKindLabel,
  knowledgeConnectionStatusMeta,
  MANAGED_SEARCH_CONNECTION_KINDS,
  RERANKER_CONNECTION_KINDS,
} from "./knowledge-connection-dialog";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { RowsSkeleton } from "./settings-card";
import type { KnowledgeConnectionOut, KnowledgeConnectionTestOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * Settings → **Knowledge connections** (V5-24, K §5.3): the workspace's Qdrant
 * / Pinecone / Weaviate vector stores, Ragie managed search (V5-45) and
 * Cohere / Voyage AI hosted re-rankers — add, test, edit and delete. A
 * knowledge base picks one of the vector-store or Ragie rows when it is
 * created (`create-kb-dialog.tsx`); the agent's Knowledge tab picks a
 * re-ranker row for the search tool (`agents/tabs/knowledge-tab.tsx`).
 *
 * Reads need `builder`; every write (add, edit, delete, test) needs `admin`,
 * like the vault keys they use (`knowledge_connections/router.py`). Builders
 * see the list read-only (D12): no write actions, and a note naming who can.
 */
export function KnowledgeConnectionsTab() {
  return (
    <RequireWrite min="builder" title="Only builders, admins and owners can see knowledge connections">
      <KnowledgeConnectionsTabInner />
    </RequireWrite>
  );
}

function connectionKindText(connection: KnowledgeConnectionOut, providers: ProviderSpec[]): string {
  const suffix = RERANKER_CONNECTION_KINDS.includes(connection.kind)
    ? " · re-ranker"
    : MANAGED_SEARCH_CONNECTION_KINDS.includes(connection.kind)
      ? " · managed search"
      : "";
  return `${knowledgeConnectionKindLabel(connection.kind, providers)}${suffix}`;
}

function ConnectionStatus({ connection }: { connection: KnowledgeConnectionOut }) {
  const meta = knowledgeConnectionStatusMeta(connection.status);
  return (
    <StatusPill tone={meta.tone} size="sm">
      {meta.label}
    </StatusPill>
  );
}

function KnowledgeConnectionsTabInner() {
  const query = useKnowledgeConnections();
  const providersQuery = useProviders();
  const providers = React.useMemo(() => providersQuery.data?.providers ?? [], [providersQuery.data]);
  const deleteMutation = useDeleteKnowledgeConnection();

  const [adding, setAdding] = React.useState(false);
  const [editing, setEditing] = React.useState<KnowledgeConnectionOut | null>(null);
  const [testing, setTesting] = React.useState<KnowledgeConnectionOut | null>(null);

  const connections = React.useMemo(() => query.data?.items ?? [], [query.data]);
  const search = useListSearch("knowledge-connections", connections, (connection) => [
    connection.name,
    connectionKindText(connection, providers),
  ]);
  const searchQuery = search.query;

  async function onDelete(connection: KnowledgeConnectionOut) {
    // A failure throws: the confirm dialog stays open and says why in plain words.
    await deleteMutation.mutateAsync(connection.id);
    toast.success(`Deleted "${connection.name}"`);
  }

  // Builders read the list; only admins get row actions (and the column that holds them).
  const { can: canWrite } = useCan("admin");
  const rowActions = (connection: KnowledgeConnectionOut) =>
    canWrite ? <RowActions connection={connection} onTest={setTesting} onEdit={setEditing} onDelete={onDelete} /> : null;

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
              <div className="truncate font-medium text-foreground">
                <Highlight text={connection.name} query={searchQuery} />
              </div>
              <div className="truncate text-caption text-text-secondary">
                <Highlight text={connectionKindText(connection, providers)} query={searchQuery} />
              </div>
            </div>
          </div>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (connection) => (
        <span title={connection.last_error ?? undefined}>
          <ConnectionStatus connection={connection} />
        </span>
      ),
    },
    {
      id: "key",
      header: "Key",
      cell: (connection) =>
        connection.credential_fingerprint ? (
          <span className="font-mono text-caption text-text-secondary">{connection.credential_fingerprint}</span>
        ) : (
          <span className="text-caption text-text-secondary">No key</span>
        ),
    },
    {
      id: "kbs",
      header: "Knowledge bases",
      align: "end",
      cell: (connection) => <span className="tabular-nums text-text-secondary">{connection.knowledge_base_count}</span>,
    },
    {
      id: "checked",
      header: "Last checked",
      cell: (connection) =>
        connection.last_checked_at ? (
          <RelativeTime iso={connection.last_checked_at} />
        ) : (
          <span className="text-text-secondary">Never</span>
        ),
    },
    ...(canWrite
      ? [
          {
            id: "actions",
            header: <span className="sr-only">Actions</span>,
            align: "end" as const,
            interactive: true,
            cell: rowActions,
          },
        ]
      : []),
  ];

  return (
    <Section
      id="knowledge-connections"
      title="Knowledge connections"
      description="Keep a knowledge base's vectors in your own Qdrant, Pinecone or Weaviate account, search documents kept in Ragie, or re-rank search results with a hosted service."
      aside={
        <IfCan min="admin" fallback={<ReadOnlyNote>{readOnlyCopy("admin", "add or change connections")}</ReadOnlyNote>}>
          <Button type="button" variant="primary" size="sm" onClick={() => setAdding(true)}>
            <PlusIcon aria-hidden="true" />
            Add connection
          </Button>
        </IfCan>
      }
    >
      <SectionRow>
        {query.isLoading ? (
          <RowsSkeleton label="Loading knowledge connections" />
        ) : query.isError ? (
          <ErrorBanner
            error={query.error}
            context={{ action: "load knowledge connections" }}
            onRetry={() => void query.refetch()}
          />
        ) : connections.length === 0 ? (
          <EmptyState
            variant="plain"
            icon={PlugIcon}
            title="No knowledge connections yet"
            description="Every knowledge base keeps its vectors on this platform until you add one."
          />
        ) : (
          <>
            <ListSearchField search={search} label="Search connections" total={connections.length} />
            {search.noMatches ? (
              <ListNoMatches search={search} items="connections" />
            ) : (
              <ResponsiveTable<KnowledgeConnectionOut>
                columns={columns}
                rows={search.filtered}
                label="Knowledge connections"
                getRowKey={(connection) => connection.id}
                renderCard={(connection) => (
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-foreground">
                          <Highlight text={connection.name} query={searchQuery} />
                        </div>
                        <div className="truncate text-caption text-text-secondary">
                          {knowledgeConnectionKindLabel(connection.kind, providers)}
                        </div>
                      </div>
                      <ConnectionStatus connection={connection} />
                    </div>
                    <div className="-mx-2">{rowActions(connection)}</div>
                  </div>
                )}
              />
            )}
          </>
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
 * the phone-width view stays actionable). All three need `admin` server-side
 * (`knowledge_connections/router.py` uses the same `WriteCtx` for test as for
 * create/update/delete), so callers render this only for admins.
 */
function RowActions({
  connection,
  onTest,
  onEdit,
  onDelete,
}: {
  connection: KnowledgeConnectionOut;
  onTest: (connection: KnowledgeConnectionOut) => void;
  onEdit: (connection: KnowledgeConnectionOut) => void;
  onDelete: (connection: KnowledgeConnectionOut) => Promise<void>;
}) {
  return (
    <div className="flex items-center justify-end gap-1">
      <Button type="button" variant="ghost" size="sm" onClick={() => onTest(connection)}>
        Test
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => onEdit(connection)}>
        Edit
      </Button>
      <ConfirmDialog
        trigger={
          <Button type="button" variant="ghost" size="sm">
            Delete
          </Button>
        }
        title={`Delete "${connection.name}"?`}
        description={
          (connection.knowledge_base_count ?? 0) > 0
            ? `${connection.knowledge_base_count} knowledge base(s) still store their vectors here. Delete or move those first.`
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
  const [error, setError] = React.useState<unknown>(null);
  const ranRef = React.useRef(false);

  React.useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    testMutation.mutateAsync(connection.id).then(setResult).catch(setError);
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
            <LoadingRow label="Testing the connection…" />
          ) : error ? (
            <ErrorBanner error={error} context={{ action: "test the connection" }} />
          ) : (
            <KnowledgeConnectionTestResultView result={result} kind={connection.kind} />
          )}
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}
