"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { BotIcon, CopyIcon, ExternalLinkIcon, MoreHorizontalIcon, TrashIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState, GatedButton, Icon, RelativeTime, ResponsiveTable, StatusChip, VendorMark } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { useAgents, useDeleteAgent, usePacks, useProviders, useUpdateAgent } from "@/components/console/lib/api-hooks";
import { errorMessage, ErrorBanner } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { AgentOut, ConnectionOut, ProviderSpec } from "@/contracts/lkap-contracts";
import { connectionTypeLabel } from "@/components/console/agents/editor/use-connection";
import { useConnections } from "@/hooks/useConnections";
import { LoadingRegion } from "@/components/shared/loading-state";

const PIPELINE_MODE_LABEL: Record<string, string> = {
  cascaded: "Cascaded",
  realtime: "Realtime",
  half_cascade: "Half-cascade",
};

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "live", label: "Live" },
  { value: "draft", label: "Draft" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

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
  return loaded ? "Unknown connection" : "—";
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
 * value, not to a round trip through the router.
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

  return [value, update] as const;
}

export function AgentsTable() {
  const { data, isLoading, isError, error, refetch } = useAgents();
  const providersQuery = useProviders();
  const packsQuery = usePacks();
  const deleteAgent = useDeleteAgent();
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  const [q, setQ] = useQueryParamState("q");
  const [status, setStatus] = useQueryParamState("status");
  const [pack, setPack] = useQueryParamState("pack");

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

  const filtered = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    return agents
      .filter((agent) => {
        if (status === "live" && !agent.published) return false;
        if (status === "draft" && agent.published) return false;
        if (pack && agent.pack_id !== pack) return false;
        if (needle.length === 0) return true;
        return agent.name.toLowerCase().includes(needle) || agent.slug.toLowerCase().includes(needle);
      })
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [agents, pack, q, status]);

  if (isLoading) {
    return (
      <LoadingRegion label="Loading agents" className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </LoadingRegion>
    );
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't load agents — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  if (agents.length === 0) {
    return (
      <EmptyState
        icon={BotIcon}
        title="No agents yet"
        description="An agent is a voice or video assistant with its own providers, instructions and tools."
        action={
          canWrite ? (
            <Button asChild>
              <Link href="/console/agents/new">New agent</Link>
            </Button>
          ) : (
            <GatedButton allowed={false} reason={writeReason}>
              New agent
            </GatedButton>
          )
        }
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
            <span className="font-medium text-foreground">{agent.name}</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="font-mono">/{agent.slug}</span>
            <span aria-hidden="true">·</span>
            <span>{packLabels.get(agent.pack_id) ?? agent.pack_id}</span>
          </div>
        </div>
      ),
    },
    {
      id: "connection",
      header: "Connection",
      cell: (agent) => <span className="text-[0.8125rem] text-muted-foreground">{connectionLabel(agent)}</span>,
    },
    {
      id: "mode",
      header: "Mode",
      cell: (agent) => (
        <span className="text-[0.8125rem] text-muted-foreground">
          {PIPELINE_MODE_LABEL[agent.config.pipeline.mode ?? "cascaded"] ?? (agent.config.pipeline.mode ?? "Cascaded")}
        </span>
      ),
    },
    {
      id: "pipeline",
      header: "Pipeline",
      cell: (agent) => {
        const ids = pipelineProviderIds(agent);
        if (ids.length === 0) return <span className="text-[0.8125rem] text-muted-foreground">—</span>;
        return (
          <div className="flex items-center gap-1">
            {ids.map((id) => (
              <VendorMark key={id} vendor={vendors.get(id) ?? id} size="sm" />
            ))}
          </div>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (agent) => (
        <StatusChip tone={agent.published ? "live" : "neutral"}>{agent.published ? "Live" : "Draft"}</StatusChip>
      ),
    },
    {
      id: "updated",
      header: "Last updated",
      cell: (agent) => <RelativeTime iso={agent.updated_at} className="text-muted-foreground" />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (agent) => <AgentRowMenu agent={agent} deleteAgent={deleteAgent} />,
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder="Search by name or slug"
          className="w-full sm:w-64"
          aria-label="Search agents"
        />
        <div className="flex items-center gap-1" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value || "all"}
              type="button"
              size="sm"
              variant={status === filter.value ? "secondary" : "ghost"}
              aria-pressed={status === filter.value}
              onClick={() => setStatus(filter.value as StatusFilter)}
            >
              {filter.label}
            </Button>
          ))}
        </div>
        {packOptions.length > 1 ? (
          <Select value={pack || "__all__"} onValueChange={(next) => setPack(next === "__all__" ? "" : next)}>
            <SelectTrigger className="w-44" aria-label="Filter by pack">
              <SelectValue placeholder="All packs" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All packs</SelectItem>
              {packOptions.map((id) => (
                <SelectItem key={id} value={id}>
                  {packLabels.get(id) ?? id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
            vendors={vendors}
            packLabels={packLabels}
            connectionLabel={connectionLabel(agent)}
            deleteAgent={deleteAgent}
          />
        )}
        empty={
          <EmptyState
            icon={BotIcon}
            title="No agents match"
            description="Try a different search term or clear the filters."
            compact
            action={
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setQ("");
                  setStatus("");
                  setPack("");
                }}
              >
                Clear filters
              </Button>
            }
          />
        }
      />
    </div>
  );
}

function AgentCard({
  agent,
  vendors,
  packLabels,
  connectionLabel,
  deleteAgent,
}: {
  agent: AgentOut;
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
          <p className="truncate font-medium text-foreground">{agent.name}</p>
          <p className="truncate font-mono text-xs text-muted-foreground">/{agent.slug}</p>
        </div>
        <StatusChip tone={agent.published ? "live" : "neutral"}>{agent.published ? "Live" : "Draft"}</StatusChip>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{packLabels.get(agent.pack_id) ?? agent.pack_id}</span>
        <span aria-hidden="true">·</span>
        <span>{connectionLabel}</span>
        <span aria-hidden="true">·</span>
        <span>{PIPELINE_MODE_LABEL[agent.config.pipeline.mode ?? "cascaded"] ?? "Cascaded"}</span>
        {ids.length > 0 ? (
          <div className="flex items-center gap-1">
            {ids.map((id) => (
              <VendorMark key={id} vendor={vendors.get(id) ?? id} size="sm" />
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex items-center justify-between gap-2 pt-1">
        <RelativeTime iso={agent.updated_at} className="text-xs text-muted-foreground" />
        <AgentRowMenu agent={agent} deleteAgent={deleteAgent} />
      </div>
    </div>
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
 * Instead, an outer `Dialog` is controlled by local state and rendered as a
 * *sibling* of the `DropdownMenu`, so closing the menu never touches it.
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
  const { canWrite } = useWriteAccess();

  async function togglePublished() {
    const next = !agent.published;
    try {
      await updateAgent.mutateAsync({ published: next });
      toast.success(next ? `${agent.name} published.` : `${agent.name} unpublished.`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setConfirmAction(null);
    }
  }

  async function confirmDelete() {
    try {
      await deleteAgent.mutateAsync(agent.id);
      toast.success(`${agent.name} deleted.`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setConfirmAction(null);
    }
  }

  async function copyPublicLink() {
    const url = `${window.location.origin}/s/${agent.slug}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Public link copied.");
    } catch {
      toast.error("Couldn't copy the link.");
    }
  }

  return (
    <Dialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={`Actions for ${agent.name}`}>
            <Icon as={MoreHorizontalIcon} size="md" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
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
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!canWrite} onSelect={() => setConfirmAction("toggle-publish")}>
            {agent.published ? "Unpublish" : "Publish"}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            disabled={!canWrite}
            onSelect={() => setConfirmAction("delete")}
          >
            Delete <Icon as={TrashIcon} size="sm" className="ml-auto" />
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DialogContent>
        {confirmAction === "delete" ? (
          <>
            <DialogHeader>
              <DialogTitle>Delete &quot;{agent.name}&quot;?</DialogTitle>
              <DialogDescription>
                Agents that have sessions can&apos;t be deleted — sessions are kept for the audit trail. Unpublish
                it instead.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setConfirmAction(null)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
                disabled={deleteAgent.isPending}
                onClick={() => void confirmDelete()}
              >
                {deleteAgent.isPending ? "Working…" : "Delete"}
              </Button>
            </DialogFooter>
          </>
        ) : confirmAction === "toggle-publish" ? (
          <>
            <DialogHeader>
              <DialogTitle>
                {agent.published ? `Unpublish "${agent.name}"?` : `Publish "${agent.name}"?`}
              </DialogTitle>
              <DialogDescription>
                {agent.published
                  ? "The public link stops answering immediately."
                  : `Publishing makes /s/${agent.slug} answer calls from anyone with the link.`}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setConfirmAction(null)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant={agent.published ? "destructive" : "default"}
                className={
                  agent.published ? "bg-destructive text-destructive-foreground hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90" : undefined
                }
                disabled={updateAgent.isPending}
                onClick={() => void togglePublished()}
              >
                {updateAgent.isPending ? "Working…" : agent.published ? "Unpublish" : "Publish"}
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
