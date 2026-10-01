"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { ArchiveRestoreIcon, CopyIcon, ExternalLinkIcon, Trash2Icon } from "lucide-react";

import { AGENTS_ICON } from "@/components/console/shell/nav-config";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { SimpleSelect } from "@/components/ui/select";
import { EmptyState, Icon, LifecycleBadge, NoMatches, RelativeTime, ResponsiveTable, RowMenu, VendorMark } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { SearchField } from "@/components/shared/search-field";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { LoadingRegion } from "@/components/shared/loading-state";
import {
  useAgents,
  useDeleteAgent,
  usePacks,
  useProviders,
  useRestoreAgent,
  useUpdateAgent,
} from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import { NewAgentButton } from "@/components/console/agents/create/new-agent-button";
import type { AgentOut, ConnectionOut, ProviderSpec } from "@/contracts/lkap-contracts";
import { connectionTypeLabel } from "@/components/console/agents/editor/use-connection";
import { useConnections } from "@/hooks/useConnections";

import { Highlight, matchesQuery, readStoredFilters, SEARCH_THRESHOLD, writeStoredFilters } from "@/components/shared/list-search";
import { EMPTY_VALUE } from "@/lib/format";

const PIPELINE_MODE_LABEL: Record<string, string> = {
  cascaded: "Cascaded",
  realtime: "Realtime",
  half_cascade: "Half-cascade",
};

/**
 * "All", "Live" and "Draft" list the active agents only; "Archived" lists the archived ones
 * (V6-30, F-5: an archived agent used to show under "All" with a Live chip). The URL keeps
 * `""` for All; the segmented control needs a real value, so it uses `"all"`.
 */
const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "live", label: "Live" },
  { value: "draft", label: "Draft" },
  { value: "archived", label: "Archived" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];
const STATUS_VALUES = new Set<string>(STATUS_FILTERS.map((filter) => filter.value));

/** Per-person filter memory (spec section 9): status and pack, validated on read. */
export const AGENT_FILTERS_STORAGE_KEY = "lkap.console.agents.filters.v1";

interface StoredAgentFilters {
  status: string;
  pack: string;
}

function parseStoredFilters(raw: unknown): StoredAgentFilters | null {
  if (!raw || typeof raw !== "object") return null;
  const { status, pack } = raw as Record<string, unknown>;
  return {
    status: typeof status === "string" && STATUS_VALUES.has(status) && status !== "all" ? status : "",
    pack: typeof pack === "string" && pack.length <= 200 ? pack : "",
  };
}

function isArchived(agent: AgentOut): boolean {
  return Boolean(agent.archived_at);
}

/** Archived wins over Live: an archived agent never shows the Live pill. Tones come from the shared map. */
function AgentStatusBadge({ agent }: { agent: AgentOut }) {
  if (isArchived(agent)) return <LifecycleBadge state="archived" />;
  return <LifecycleBadge state={agent.published ? "live" : "draft"} />;
}

/**
 * The Connection column: the bound connection's name and type, looked up in
 * the workspace's connection list (`useConnections`). An agent with no
 * `connection_id` runs on the workspace default. Never the raw id — people
 * know their connections by name.
 */
function connectionLabels(connections: readonly ConnectionOut[] | undefined): Map<string | null, string> {
  const labels = new Map<string | null, string>();
  for (const connection of connections ?? []) {
    labels.set(connection.id, `${connection.name} · ${connectionTypeLabel(connection)}`);
    if (connection.is_default) labels.set(null, `${connection.name} (default)`);
  }
  if (!labels.has(null)) labels.set(null, "Workspace default");
  return labels;
}

function agentConnectionLabel(agent: AgentOut, labels: Map<string | null, string>, loaded: boolean): string {
  const id = typeof agent.connection_id === "string" && agent.connection_id.trim() ? agent.connection_id.trim() : null;
  const label = labels.get(id);
  if (label) return label;
  return loaded ? "Unknown connection" : EMPTY_VALUE;
}

/** Non-null pipeline slots in display order, most agents show 1–3. */
function pipelineProviderIds(agent: AgentOut): string[] {
  const { pipeline } = agent.config;
  if ((pipeline.mode ?? "cascaded") === "realtime") {
    return pipeline.realtime ? [pipeline.realtime.provider_id] : [];
  }
  return [pipeline.stt, pipeline.llm, pipeline.tts]
    .filter((ref): ref is NonNullable<typeof ref> => Boolean(ref))
    .map((ref) => ref.provider_id);
}

function vendorLookup(providers: ProviderSpec[] | undefined): Map<string, string> {
  const map = new Map<string, string>();
  for (const spec of providers ?? []) map.set(spec.id, spec.vendor);
  return map;
}

/**
 * Reads a query-string param and writes it back on change so the list's
 * search/filters are shareable and restored by the back button
 * (docs/UI_UX_SPEC.md §3.5). Filtering itself always reacts to the local
 * value, not to a round trip through the router. `restore` sets the local
 * value only (remembered filters), leaving the URL alone.
 */
function useQueryParamState(key: string) {
  const router = useRouter();
  const pathname = usePathname() ?? "/console/agents";
  const searchParams = useSearchParams();
  const [value, setValue] = React.useState(() => searchParams?.get(key) ?? "");

  const update = React.useCallback(
    (next: string) => {
      setValue(next);
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (next) params.set(key, next);
      else params.delete(key);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [key, pathname, router, searchParams],
  );

  return [value, update, setValue] as const;
}

/** Mirrors the list's layout: the toolbar, then a table header and rows (spec section 8.1). */
function AgentsTableSkeleton() {
  return (
    <LoadingRegion label="Loading agents" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-[34px] w-64 max-w-full rounded" />
        <Skeleton className="h-[34px] w-72 max-w-full rounded" />
      </div>
      <div className="hidden flex-col md:flex">
        <div className="flex h-9 items-center gap-6 border-b border-border px-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 w-16" />
        </div>
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="flex h-14 items-center gap-6 border-b border-border px-2">
            <div className="flex w-56 flex-col gap-1.5">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-3 w-28" />
            </div>
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-3.5 w-16" />
            <Skeleton className="h-5 w-20 rounded-pill" />
            <Skeleton className="ml-auto h-5 w-14 rounded-pill" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2 md:hidden">
        {[0, 1, 2].map((card) => (
          <Skeleton key={card} className="h-28 w-full rounded-lg" />
        ))}
      </div>
    </LoadingRegion>
  );
}

export function AgentsTable() {
  const { data, isLoading, isError, error, refetch } = useAgents();
  const providersQuery = useProviders();
  const packsQuery = usePacks();
  const deleteAgent = useDeleteAgent();
  const searchParams = useSearchParams();

  const [q, setQ] = useQueryParamState("q");
  const [statusParam, setStatusParam, restoreStatus] = useQueryParamState("status");
  const [pack, setPackParam, restorePack] = useQueryParamState("pack");
  // An unknown `?status=` reads as All.
  const status: StatusFilter = STATUS_VALUES.has(statusParam) ? (statusParam as StatusFilter) : "all";

  const connectionsQuery = useConnections();
  const vendors = React.useMemo(() => vendorLookup(providersQuery.data?.providers), [providersQuery.data]);
  const connections = React.useMemo(() => connectionLabels(connectionsQuery.data?.items), [connectionsQuery.data]);
  const connectionLabel = (agent: AgentOut) => agentConnectionLabel(agent, connections, connectionsQuery.isSuccess);
  const packLabels = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const item of packsQuery.data?.items ?? []) map.set(item.manifest.id, item.manifest.name);
    return map;
  }, [packsQuery.data]);

  const agents = React.useMemo(() => data?.items ?? [], [data]);

  const packOptions = React.useMemo(() => {
    const ids = new Set<string>();
    for (const agent of agents) ids.add(agent.pack_id);
    return Array.from(ids).sort();
  }, [agents]);

  // Remembered filters: applied once the agents arrive, only when the URL names none.
  const restored = React.useRef(false);
  React.useLayoutEffect(() => {
    if (restored.current || !data) return;
    restored.current = true;
    if (searchParams?.has("status") || searchParams?.has("pack")) return;
    const saved = readStoredFilters(AGENT_FILTERS_STORAGE_KEY, parseStoredFilters);
    if (!saved) return;
    if (saved.status) restoreStatus(saved.status);
    if (saved.pack && data.items.some((agent) => agent.pack_id === saved.pack)) restorePack(saved.pack);
  }, [data, restorePack, restoreStatus, searchParams]);

  const setStatus = (next: StatusFilter) => {
    const value = next === "all" ? "" : next;
    setStatusParam(value);
    writeStoredFilters(AGENT_FILTERS_STORAGE_KEY, { status: value, pack });
  };
  const setPack = (next: string) => {
    setPackParam(next);
    writeStoredFilters(AGENT_FILTERS_STORAGE_KEY, { status: status === "all" ? "" : status, pack: next });
  };
  const clearFilters = () => {
    setQ("");
    setStatusParam("");
    setPackParam("");
    writeStoredFilters(AGENT_FILTERS_STORAGE_KEY, { status: "", pack: "" });
  };

  const counts = React.useMemo(() => {
    const active = agents.filter((agent) => !isArchived(agent));
    return {
      all: active.length,
      live: active.filter((agent) => agent.published).length,
      draft: active.filter((agent) => !agent.published).length,
      archived: agents.length - active.length,
    } satisfies Record<StatusFilter, number>;
  }, [agents]);

  const filtered = React.useMemo(
    () =>
      agents
        .filter((agent) => {
          if ((status === "archived") !== isArchived(agent)) return false;
          if (status === "live" && !agent.published) return false;
          if (status === "draft" && agent.published) return false;
          if (pack && agent.pack_id !== pack) return false;
          return matchesQuery([agent.name, agent.slug], q);
        })
        .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()),
    [agents, pack, status, q],
  );

  if (isLoading) return <AgentsTableSkeleton />;

  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load agents" }} onRetry={() => refetch()} />;
  }

  if (agents.length === 0) {
    return (
      <EmptyState
        icon={AGENTS_ICON}
        title="No agents yet"
        description="An agent is a voice or video assistant with its own providers, instructions and tools."
        action={<NewAgentButton variant="secondary" />}
      />
    );
  }

  const columns: ResponsiveTableColumn<AgentOut>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (agent) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">
              <Highlight text={agent.name} query={q} />
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-caption text-text-tertiary">
            <span className="font-mono">
              /<Highlight text={agent.slug} query={q} />
            </span>
            <span aria-hidden="true">·</span>
            <span>{packLabels.get(agent.pack_id) ?? agent.pack_id}</span>
          </div>
        </div>
      ),
    },
    {
      id: "connection",
      header: "Connection",
      cell: (agent) => <span className="text-label text-text-secondary">{connectionLabel(agent)}</span>,
    },
    {
      id: "mode",
      header: "Mode",
      cell: (agent) => (
        <span className="text-label text-text-secondary">
          {PIPELINE_MODE_LABEL[agent.config.pipeline.mode ?? "cascaded"] ?? (agent.config.pipeline.mode ?? "Cascaded")}
        </span>
      ),
    },
    {
      id: "pipeline",
      header: "Pipeline",
      cell: (agent) => {
        const ids = pipelineProviderIds(agent);
        if (ids.length === 0) return <span className="text-label text-text-secondary">{EMPTY_VALUE}</span>;
        return (
          <div className="flex items-center gap-1">
            {ids.map((id) => (
              <VendorMark key={id} vendor={vendors.get(id) ?? id} size="sm" labelled />
            ))}
          </div>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (agent) => <AgentStatusBadge agent={agent} />,
    },
    {
      id: "updated",
      header: "Last updated",
      cell: (agent) => <RelativeTime iso={agent.updated_at} className="text-text-secondary" />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (agent) =>
        isArchived(agent) ? <RestoreAgentButton agent={agent} /> : <AgentRowMenu agent={agent} deleteAgent={deleteAgent} />,
    },
  ];

  const onlyArchived = status !== "archived" && counts.archived > 0 && counts.archived === agents.length;
  const showSearch = agents.length >= SEARCH_THRESHOLD || q !== "";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {showSearch ? (
          <SearchField
            value={q}
            onValueChange={setQ}
            placeholder="Search by name or slug"
            aria-label="Search agents"
            wrapperClassName="w-full sm:w-64"
          />
        ) : null}
        <SegmentedControl<StatusFilter>
          label="Filter by status"
          value={status}
          onValueChange={setStatus}
          options={STATUS_FILTERS.map((filter) => ({ ...filter, count: counts[filter.value] }))}
        />
        {packOptions.length > 1 ? (
          <SimpleSelect
            value={pack}
            onValueChange={setPack}
            aria-label="Filter by pack"
            className="w-full sm:w-44"
            options={[
              { value: "", label: "All packs" },
              ...packOptions.map((id) => ({ value: id, label: packLabels.get(id) ?? id })),
            ]}
          />
        ) : null}
      </div>

      <ResponsiveTable<AgentOut>
        columns={columns}
        rows={filtered}
        label="Agents"
        getRowKey={(agent) => agent.id}
        rowHref={(agent) => `/console/agents/${agent.id}`}
        renderCard={(agent) => (
          <AgentCard
            agent={agent}
            query={q}
            vendors={vendors}
            packLabels={packLabels}
            connectionLabel={connectionLabel(agent)}
            deleteAgent={deleteAgent}
          />
        )}
        empty={
          onlyArchived ? (
            <EmptyState
              icon={AGENTS_ICON}
              title="No active agents"
              description="Every agent here is archived. Open Archived to see them or restore one."
              action={
                <Button type="button" variant="secondary" size="sm" onClick={() => setStatus("archived")}>
                  Show archived
                </Button>
              }
            />
          ) : (
            <NoMatches items="agents" query={q} onClear={clearFilters} />
          )
        }
      />
    </div>
  );
}

function AgentCard({
  agent,
  query,
  vendors,
  packLabels,
  connectionLabel,
  deleteAgent,
}: {
  agent: AgentOut;
  query: string;
  vendors: Map<string, string>;
  packLabels: Map<string, string>;
  connectionLabel: string;
  deleteAgent: ReturnType<typeof useDeleteAgent>;
}) {
  const ids = pipelineProviderIds(agent);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-foreground">
            <Highlight text={agent.name} query={query} />
          </p>
          <p className="truncate font-mono text-caption text-text-tertiary">
            /<Highlight text={agent.slug} query={query} />
          </p>
        </div>
        <AgentStatusBadge agent={agent} />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-secondary">
        <span>{packLabels.get(agent.pack_id) ?? agent.pack_id}</span>
        <span aria-hidden="true">·</span>
        <span className="min-w-0 break-words">{connectionLabel}</span>
        <span aria-hidden="true">·</span>
        <span>{PIPELINE_MODE_LABEL[agent.config.pipeline.mode ?? "cascaded"] ?? "Cascaded"}</span>
        {ids.length > 0 ? (
          <div className="flex items-center gap-1">
            {ids.map((id) => (
              <VendorMark key={id} vendor={vendors.get(id) ?? id} size="sm" labelled />
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex items-center justify-between gap-2 pt-1">
        <RelativeTime iso={agent.updated_at} className="text-caption text-text-tertiary" />
        {isArchived(agent) ? <RestoreAgentButton agent={agent} /> : <AgentRowMenu agent={agent} deleteAgent={deleteAgent} />}
      </div>
    </div>
  );
}

/**
 * An archived agent's one action (V6-30): Restore, confirmed in a dialog. It clears
 * `archived_at` (`POST /agents/{id}/unarchive`); a published agent's public link then answers
 * again, which the dialog says. Hidden for people who can't write (decision D12).
 */
function RestoreAgentButton({ agent }: { agent: AgentOut }) {
  const restore = useRestoreAgent();
  const { can } = useCan();
  const [open, setOpen] = React.useState(false);
  if (!can) return null;

  return (
    <>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-label={`Restore ${agent.name}`}
        onClick={() => setOpen(true)}
      >
        <Icon as={ArchiveRestoreIcon} size="sm" />
        Restore
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        destructive={false}
        title={`Restore "${agent.name}"?`}
        description={
          agent.published
            ? `It moves back to your agents. It is still published, so /s/${agent.slug} answers calls again.`
            : "It moves back to your agents as a draft."
        }
        confirmLabel="Restore"
        busyLabel="Restoring…"
        onConfirm={async () => {
          await restore.mutateAsync(agent.id);
          toast.success(`${agent.name} restored.`);
        }}
      />
    </>
  );
}

type RowConfirmAction = "toggle-publish" | "delete" | null;

/**
 * The row's "Publish/Unpublish" and "Delete" confirmations are NOT nested
 * inside `DropdownMenuContent` (the seemingly obvious `ConfirmDialog` +
 * `DialogTrigger asChild` composition): opening a focus-trapping `Dialog`
 * while it is still a descendant of the menu makes Radix's dismissable layer
 * treat the focus move as an outside interaction and close the menu, which
 * unmounts the dialog with it before the confirm button can ever be clicked.
 * Instead, the controlled `ConfirmDialog`s are *siblings* of the menu, so
 * closing the menu never touches them. Publish and Delete are hidden for
 * people who can't write (decision D12).
 */
function AgentRowMenu({
  agent,
  deleteAgent,
}: {
  agent: AgentOut;
  deleteAgent: ReturnType<typeof useDeleteAgent>;
}) {
  const updateAgent = useUpdateAgent(agent.id);
  const [confirmAction, setConfirmAction] = React.useState<RowConfirmAction>(null);
  const { can } = useCan();
  const closeConfirm = (open: boolean) => {
    if (!open) setConfirmAction(null);
  };

  async function copyPublicLink() {
    const url = `${window.location.origin}/s/${agent.slug}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Public link copied.");
    } catch {
      toast.error("Couldn't copy the link. Copy it from the address bar of the public page instead.");
    }
  }

  return (
    <>
      <RowMenu
        label={`Actions for ${agent.name}`}
        destructive={can ? { label: "Delete", icon: Trash2Icon, onSelect: () => setConfirmAction("delete") } : undefined}
      >
        <DropdownMenuItem asChild>
          <Link href={`/console/agents/${agent.id}`}>Open</Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={`/s/${agent.slug}?mode=test`} target="_blank" rel="noopener noreferrer">
            Test call <Icon as={ExternalLinkIcon} size="sm" className="ml-auto" />
          </a>
        </DropdownMenuItem>
        {agent.published ? (
          <DropdownMenuItem onSelect={() => void copyPublicLink()}>
            Copy public link <Icon as={CopyIcon} size="sm" className="ml-auto" />
          </DropdownMenuItem>
        ) : null}
        {can ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setConfirmAction("toggle-publish")}>
              {agent.published ? "Unpublish" : "Publish"}
            </DropdownMenuItem>
          </>
        ) : null}
      </RowMenu>

      <ConfirmDialog
        open={confirmAction === "delete"}
        onOpenChange={closeConfirm}
        title={`Delete "${agent.name}"?`}
        description="Agents that have sessions can't be deleted. Sessions are kept for the audit trail. Unpublish it instead."
        confirmLabel="Delete"
        busyLabel="Deleting…"
        onConfirm={async () => {
          await deleteAgent.mutateAsync(agent.id);
          toast.success(`${agent.name} deleted.`);
        }}
      />
      <ConfirmDialog
        open={confirmAction === "toggle-publish"}
        onOpenChange={closeConfirm}
        destructive={agent.published}
        title={agent.published ? `Unpublish "${agent.name}"?` : `Publish "${agent.name}"?`}
        description={
          agent.published
            ? "The public link stops answering immediately."
            : `Publishing makes /s/${agent.slug} answer calls from anyone with the link.`
        }
        confirmLabel={agent.published ? "Unpublish" : "Publish"}
        onConfirm={async () => {
          const next = !agent.published;
          await updateAgent.mutateAsync({ published: next });
          toast.success(next ? `${agent.name} published.` : `${agent.name} unpublished.`);
        }}
      />
    </>
  );
}
