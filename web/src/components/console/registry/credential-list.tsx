"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { FlaskConicalIcon, KeyRoundIcon, MoreHorizontalIcon, PencilIcon, RefreshCwIcon, TrashIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useSetBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useAgents, useCredentials, useDeleteCredential, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { CredentialSheet, type CredentialSheetMode } from "@/components/console/registry/credential-sheet";
import { OUTCOME_LABEL, OUTCOME_TONE, useCredentialTest } from "@/components/console/registry/credential-test";
import { KIND_LABEL, kindRank } from "@/components/console/registry/provider-meta";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { pluralize } from "@/lib/format";
import { ApiError } from "@/lib/api";
import type { AgentOut, CredentialOut, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";

/** How long the inline test result stays as a chip before it settles to "Tested … ago" (§4.5). */
export const TEST_CHIP_MS = 10_000;

export interface CredentialUsage {
  agents: { id: string; name: string }[];
  tools: { id: string; name: string }[];
}

const NO_USAGE: CredentialUsage = { agents: [], tools: [] };

function isProviderRefLike(value: unknown): value is { credential_id?: string | null } {
  return typeof value === "object" && value !== null && "provider_id" in value;
}

/**
 * Client-side join (§4.5): which agents (any `config.pipeline.*` slot) and
 * which tools (`definition.credential_id`, the `http-tool-secret` binding)
 * reference each credential. Replace with an api count if one appears.
 */
export function credentialUsage(agents: AgentOut[], tools: ToolOut[]): Map<string, CredentialUsage> {
  const map = new Map<string, CredentialUsage>();
  const entry = (id: string) => {
    let usage = map.get(id);
    if (!usage) {
      usage = { agents: [], tools: [] };
      map.set(id, usage);
    }
    return usage;
  };
  for (const agent of agents) {
    const seen = new Set<string>();
    for (const slot of Object.values(agent.config?.pipeline ?? {})) {
      if (isProviderRefLike(slot) && slot.credential_id && !seen.has(slot.credential_id)) {
        seen.add(slot.credential_id);
        entry(slot.credential_id).agents.push({ id: agent.id, name: agent.name });
      }
    }
  }
  for (const tool of tools) {
    const credentialId = tool.definition?.credential_id;
    if (credentialId) entry(credentialId).tools.push({ id: tool.id, name: tool.name });
  }
  return map;
}

function usageText(usage: CredentialUsage): string {
  const parts: string[] = [];
  if (usage.agents.length > 0) parts.push(pluralize(usage.agents.length, "agent", "agents"));
  if (usage.tools.length > 0) parts.push(pluralize(usage.tools.length, "tool", "tools"));
  return parts.length > 0 ? `Used by ${parts.join(" · ")}` : "Not used";
}

interface SheetState {
  mode: CredentialSheetMode;
  credential?: CredentialOut;
}

/**
 * `/console/keys` (served there, not /console/credentials, per the operator permission rule; docs/UI_UX_SPEC.md §4.5; UI_UX_SPEC-V2-AMENDMENTS
 * §1: stays the flat key list, linked from Providers). One `ResponsiveTable`
 * sorted by kind then provider; the kind is a column rather than a group.
 * Row actions: Test (inline result for 10 s, then "Tested … ago"), Rotate,
 * Rename, Delete (a 409 names the agents that still use the key).
 */
export function CredentialList() {
  useSetBreadcrumbs([{ label: "Providers", href: "/console/providers" }, { label: "Credentials" }]);

  const credentialsQuery = useCredentials();
  const providersQuery = useProviders();
  const agentsQuery = useAgents();
  const toolsQuery = useTools();
  const [sheet, setSheet] = React.useState<SheetState | null>(null);
  const [deleting, setDeleting] = React.useState<CredentialOut | null>(null);
  // Credentials need `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  const specs = React.useMemo(() => {
    const map = new Map<string, ProviderSpec>();
    for (const spec of providersQuery.data?.providers ?? []) map.set(spec.id, spec);
    return map;
  }, [providersQuery.data]);

  const usage = React.useMemo(
    () => credentialUsage(agentsQuery.data?.items ?? [], toolsQuery.data?.items ?? []),
    [agentsQuery.data, toolsQuery.data],
  );

  const rows = React.useMemo(() => {
    const items = [...(credentialsQuery.data?.items ?? [])];
    return items.sort((a, b) => {
      const sa = specs.get(a.provider_id);
      const sb = specs.get(b.provider_id);
      const byKind = kindRank(sa?.kind ?? "secret_bag") - kindRank(sb?.kind ?? "secret_bag");
      if (byKind !== 0) return byKind;
      const byProvider = (sa?.label ?? a.provider_id).localeCompare(sb?.label ?? b.provider_id);
      return byProvider !== 0 ? byProvider : a.label.localeCompare(b.label);
    });
  }, [credentialsQuery.data, specs]);

  const addButton = (
    <Button
      type="button"
      disabled={!canWrite}
      title={canWrite ? undefined : writeReason}
      onClick={() => setSheet({ mode: "create" })}
    >
      Add credential
    </Button>
  );

  const openSheet = (mode: CredentialSheetMode, credential: CredentialOut) => setSheet({ mode, credential });

  let body: React.ReactNode;
  if (credentialsQuery.isLoading) {
    body = (
      <LoadingRegion label="Loading credentials" className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </LoadingRegion>
    );
  } else if (credentialsQuery.isError) {
    body = (
      <ErrorBanner
        message={`Couldn't load credentials — ${errorMessage(credentialsQuery.error)}`}
        onRetry={() => credentialsQuery.refetch()}
      />
    );
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        icon={KeyRoundIcon}
        title="No credentials yet"
        description="Add a vendor key to run speech, language, voice or avatar providers on your own account. LiveKit Inference needs no key."
        action={addButton}
      />
    );
  } else {
    const columns: ResponsiveTableColumn<CredentialOut>[] = [
      {
        id: "credential",
        header: "Credential",
        cell: (row) => <CredentialIdentity credential={row} spec={specs.get(row.provider_id)} />,
      },
      {
        id: "kind",
        header: "Kind",
        cell: (row) => {
          const spec = specs.get(row.provider_id);
          return <span className="text-[0.8125rem] text-muted-foreground">{spec ? KIND_LABEL[spec.kind] : "—"}</span>;
        },
      },
      {
        id: "fingerprint",
        header: "Fingerprint",
        interactive: true,
        cell: (row) => <Fingerprint value={row.fingerprint} />,
      },
      {
        id: "usage",
        header: "Used by",
        interactive: true,
        cell: (row) => <UsageCell usage={usage.get(row.id) ?? NO_USAGE} />,
      },
      {
        id: "created",
        header: "Created",
        cell: (row) => <RelativeTime iso={row.created_at} className="text-[0.8125rem] text-muted-foreground" />,
      },
      {
        id: "test",
        header: "Last test",
        cell: (row) => <TestStatus credentialId={row.id} />,
      },
      {
        id: "actions",
        header: <span className="sr-only">Actions</span>,
        align: "end",
        interactive: true,
        cell: (row) => <RowActions credential={row} onOpenSheet={openSheet} onDelete={setDeleting} />,
      },
    ];

    body = (
      <ResponsiveTable
        label="Credentials"
        columns={columns}
        rows={rows}
        getRowKey={(row) => row.id}
        renderCard={(row) => (
          <div className="flex flex-col gap-2 p-4">
            <div className="flex items-start justify-between gap-2">
              <CredentialIdentity credential={row} spec={specs.get(row.provider_id)} />
              <RowActions credential={row} onOpenSheet={openSheet} onDelete={setDeleting} />
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-[2.125rem] text-[0.8125rem] text-muted-foreground">
              <Fingerprint value={row.fingerprint} />
              <UsageCell usage={usage.get(row.id) ?? NO_USAGE} />
            </div>
            <div className="pl-[2.125rem]">
              <TestStatus credentialId={row.id} />
            </div>
          </div>
        )}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="Credentials"
        description="Vendor keys your agents and tools use. Secrets are encrypted at rest; only a fingerprint is ever shown."
        actions={rows.length > 0 ? addButton : undefined}
      />
      {body}
      <CredentialSheet
        open={sheet !== null}
        onOpenChange={(open) => {
          if (!open) setSheet(null);
        }}
        mode={sheet?.mode ?? "create"}
        credential={sheet?.credential}
        spec={sheet?.credential ? specs.get(sheet.credential.provider_id) : undefined}
      />
      <DeleteCredentialDialog
        credential={deleting}
        usage={deleting ? (usage.get(deleting.id) ?? NO_USAGE) : NO_USAGE}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}

function CredentialIdentity({ credential, spec }: { credential: CredentialOut; spec: ProviderSpec | undefined }) {
  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <VendorMark vendor={spec?.vendor ?? credential.provider_id} size="md" className="mt-0.5" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-foreground">{credential.label}</span>
        <span className="truncate text-xs text-muted-foreground">{spec?.label ?? credential.provider_id}</span>
      </div>
    </div>
  );
}

/** Fingerprints are not secrets (§4.5), so they are copyable. */
function Fingerprint({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <span className="font-mono text-[0.8125rem] text-muted-foreground tabular-nums">{value}</span>
      <CopyButton value={value} label="Copy fingerprint" size="xs" />
    </span>
  );
}

function UsageCell({ usage }: { usage: CredentialUsage }) {
  const text = usageText(usage);
  if (usage.agents.length === 0 && usage.tools.length === 0) {
    return <span className="text-[0.8125rem] text-muted-foreground">{text}</span>;
  }
  const names = [...usage.agents.map((a) => a.name), ...usage.tools.map((t) => t.name)].join(", ");
  return (
    <Link
      href="/console/agents"
      title={names}
      className="rounded-xs text-[0.8125rem] text-foreground underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
    >
      {text}
    </Link>
  );
}

/** Result chip for 10 s after a test, then a quiet "Tested … ago". */
function TestStatus({ credentialId }: { credentialId: string }) {
  const { last, pending } = useCredentialTest(credentialId);
  const [fresh, setFresh] = React.useState(false);

  React.useEffect(() => {
    if (!last) return;
    const remaining = TEST_CHIP_MS - (Date.now() - last.receivedAt);
    if (remaining <= 0) {
      setFresh(false);
      return;
    }
    setFresh(true);
    const timer = setTimeout(() => setFresh(false), remaining);
    return () => clearTimeout(timer);
  }, [last]);

  return (
    <span role="status" aria-live="polite" className="inline-flex min-h-5 items-center">
      {pending ? (
        <span className="text-[0.8125rem] text-muted-foreground">Testing…</span>
      ) : last && fresh ? (
        <StatusChip tone={OUTCOME_TONE[last.outcome]} dot size="sm" className="max-w-64">
          <span className="truncate" title={last.message}>
            {last.outcome === "passed" || last.outcome === "timed-out" ? OUTCOME_LABEL[last.outcome] : last.message || OUTCOME_LABEL[last.outcome]}
          </span>
        </StatusChip>
      ) : last ? (
        <span className="text-[0.8125rem] text-muted-foreground" title={last.message}>
          {OUTCOME_LABEL[last.outcome]} · <RelativeTime iso={last.testedAt} />
        </span>
      ) : (
        <span className="text-[0.8125rem] text-muted-foreground">Not tested</span>
      )}
    </span>
  );
}

function RowActions({
  credential,
  onOpenSheet,
  onDelete,
}: {
  credential: CredentialOut;
  onOpenSheet: (mode: CredentialSheetMode, credential: CredentialOut) => void;
  onDelete: (credential: CredentialOut) => void;
}) {
  const { run, pending } = useCredentialTest(credential.id);
  const { canWrite } = useWriteAccess("admin");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`Actions for ${credential.label}`}>
          <MoreHorizontalIcon aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem disabled={pending} onSelect={() => void run()}>
          <FlaskConicalIcon aria-hidden="true" />
          Test
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!canWrite} onSelect={() => onOpenSheet("rotate", credential)}>
          <RefreshCwIcon aria-hidden="true" />
          Rotate
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!canWrite} onSelect={() => onOpenSheet("rename", credential)}>
          <PencilIcon aria-hidden="true" />
          Rename
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" disabled={!canWrite} onSelect={() => onDelete(credential)}>
          <TrashIcon aria-hidden="true" />
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function DeleteCredentialDialog({
  credential,
  usage,
  onClose,
}: {
  credential: CredentialOut | null;
  usage: CredentialUsage;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteCredential();
  const [conflict, setConflict] = React.useState<string | null>(null);

  React.useEffect(() => {
    setConflict(null);
  }, [credential]);

  async function confirm() {
    if (!credential) return;
    try {
      await deleteMutation.mutateAsync(credential.id);
      toast.success("Credential deleted");
      onClose();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(error.message);
        return;
      }
      toast.error(`Couldn't delete — ${errorMessage(error)}`);
    }
  }

  const inUse = usage.agents.length > 0 || usage.tools.length > 0;

  return (
    <Dialog open={credential !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {credential?.label}</DialogTitle>
          <DialogDescription>
            The key is removed from the vault. Agents and tools that use it stop working until they get another key.
          </DialogDescription>
        </DialogHeader>
        {conflict ? (
          <div role="alert" className="flex flex-col gap-1.5 rounded-md bg-danger-soft px-3 py-2 text-[0.8125rem] text-danger-text">
            {usage.agents.length > 0 ? (
              <p>
                Used by{" "}
                {usage.agents.map((agent, index) => (
                  <React.Fragment key={agent.id}>
                    {index > 0 ? ", " : null}
                    <Link
                      href={`/console/agents/${agent.id}?section=providers`}
                      className="font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {agent.name}
                    </Link>
                  </React.Fragment>
                ))}{" "}
                — change those agents first.
              </p>
            ) : (
              <p>{conflict}</p>
            )}
          </div>
        ) : inUse ? (
          <p className="text-[0.8125rem] text-warning-text">{usageText(usage)}.</p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={deleteMutation.isPending}
            onClick={() => void confirm()}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete credential"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
