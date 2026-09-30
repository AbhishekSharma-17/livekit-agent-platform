"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { FlaskConicalIcon, KeyRoundIcon, PencilIcon, PlugIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { FormError } from "@/components/shared/field";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { NewResourceButton } from "@/components/shared/new-resource-button";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { RowMenu, type RowMenuAction } from "@/components/shared/row-menu";
import { StatusPill } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useSetBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useAgents, useCredentials, useDeleteCredential, useDisableApps, useEnableApps, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { CredentialDialog, type CredentialDialogMode } from "@/components/console/registry/credential-dialog";
import { OUTCOME_LABEL, OUTCOME_TONE, useCredentialTest } from "@/components/console/registry/credential-test";
import { KIND_LABEL, credentialDisplay, kindRank } from "@/components/console/registry/provider-meta";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import { RowsSkeleton } from "@/components/console/registry/rows-skeleton";
import { EnableComposioDialog } from "@/components/console/tools/apps/enable-composio-dialog";
import { appsErrorMessage, composioStatusChip, useComposioStatus } from "@/components/console/tools/apps/use-composio";
import { pluralize } from "@/lib/format";
import { ApiError } from "@/lib/api";
import type { AgentOut, CredentialOut, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

/**
 * `lkap_contracts.tool_providers` module-level constants (not pydantic
 * models, so `lkap_contracts.export` never puts them in the generated
 * `.d.ts` — that file carries types only). Mirrored here as literals rather
 * than imported.
 */
const COMPOSIO_PROVIDER_ID = "composio";
const TOOL_PROVIDER_ACCOUNT = "tool-provider-account";
//: `lkap_contracts.providers.MCP_OAUTH_PROVIDER_ID` — same reasoning as `TOOL_PROVIDER_ACCOUNT`
//: below (docs/v5/_asks.md #108, V5-14/V5-21): written only by the MCP sign-in callback, no
//: secret fields, no generic Test/Rotate/Delete story. The api doesn't refuse a hand-made row
//: yet (that's V5-27's), so this list hides it client-side in the meantime.
const MCP_OAUTH_PROVIDER_ID = "mcp-oauth";

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
    // V6-16: a lookup-table tool binds no credential.
    const credentialId =
      tool.definition && "credential_id" in tool.definition ? tool.definition.credential_id : undefined;
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

interface DialogState {
  mode: CredentialDialogMode;
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
  const [dialog, setDialog] = React.useState<DialogState | null>(null);
  const [deleting, setDeleting] = React.useState<CredentialOut | null>(null);

  const registry = React.useMemo(() => providersQuery.data?.providers ?? [], [providersQuery.data]);

  const specs = React.useMemo(() => {
    const map = new Map<string, ProviderSpec>();
    for (const spec of registry) map.set(spec.id, spec);
    return map;
  }, [registry]);

  const usage = React.useMemo(
    () => credentialUsage(agentsQuery.data?.items ?? [], toolsQuery.data?.items ?? []),
    [agentsQuery.data, toolsQuery.data],
  );

  const rows = React.useMemo(() => {
    // Composio's connected apps (`provider_id: "tool-provider-account"`) and an MCP
    // server's sign-in (`provider_id: "mcp-oauth"`) are not vault keys a builder
    // manages here — they carry no secret and have no generic Test/Rotate/Delete
    // story (docs/v5/_asks.md #4, #108: the api routes don't refuse hand-made rows
    // of either kind yet, so this list hides them client-side until they do). The
    // workspace's own Composio key (`provider_id: "composio"`) is an ordinary row,
    // just with extra actions below.
    const items = (credentialsQuery.data?.items ?? []).filter(
      (c) => c.provider_id !== TOOL_PROVIDER_ACCOUNT && c.provider_id !== MCP_OAUTH_PROVIDER_ID,
    );
    const titleFor = (spec: ProviderSpec | undefined, providerId: string) =>
      spec ? credentialDisplay(spec, registry).title : providerId;
    return items.sort((a, b) => {
      const sa = specs.get(a.provider_id);
      const sb = specs.get(b.provider_id);
      const byKind = kindRank(sa?.kind ?? "secret_bag") - kindRank(sb?.kind ?? "secret_bag");
      if (byKind !== 0) return byKind;
      const byProvider = titleFor(sa, a.provider_id).localeCompare(titleFor(sb, b.provider_id));
      return byProvider !== 0 ? byProvider : a.label.localeCompare(b.label);
    });
  }, [credentialsQuery.data, specs, registry]);

  const titleOf = (row: CredentialOut) => {
    const spec = specs.get(row.provider_id);
    return spec ? credentialDisplay(spec, registry).title : row.provider_id;
  };
  const kindsOf = (row: CredentialOut) => {
    const spec = specs.get(row.provider_id);
    return spec ? [KIND_LABEL[spec.kind], ...credentialDisplay(spec, registry).usedBy].join(" ") : "";
  };
  const search = useListSearch("provider-keys", rows, (row) => [row.label, titleOf(row), kindsOf(row), row.fingerprint]);
  const query = search.query;

  // Credentials are admin writes server-side (`auth/roles.py::ROUTE_POLICY`):
  // below admin the page-level action is a note that names the next step (D12).
  const addButton = (
    <NewResourceButton min="admin" readOnlyNote="Ask an admin to add credentials." onClick={() => setDialog({ mode: "create" })}>
      Add credential
    </NewResourceButton>
  );

  const openDialog = (mode: CredentialDialogMode, credential: CredentialOut) => setDialog({ mode, credential });

  let body: React.ReactNode;
  if (credentialsQuery.isLoading) {
    body = <RowsSkeleton label="Loading credentials" mark />;
  } else if (credentialsQuery.isError) {
    body = (
      <ErrorBanner
        error={credentialsQuery.error}
        context={{ action: "load credentials" }}
        onRetry={() => void credentialsQuery.refetch()}
      />
    );
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        icon={KeyRoundIcon}
        title="No credentials yet"
        description="Add a vendor key to run speech, language, voice or avatar providers on your own account."
        action={addButton}
      />
    );
  } else {
    const columns: ResponsiveTableColumn<CredentialOut>[] = [
      {
        id: "credential",
        header: "Credential",
        cell: (row) => <CredentialIdentity credential={row} spec={specs.get(row.provider_id)} registry={registry} query={query} />,
      },
      {
        id: "kind",
        header: "Kind",
        cell: (row) => <CredentialKind spec={specs.get(row.provider_id)} registry={registry} />,
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
        id: "added",
        header: "Added",
        cell: (row) => <RelativeTime iso={row.created_at} className="text-label text-text-secondary" />,
      },
      {
        id: "used",
        header: "Last used",
        cell: (row) => <LastUsed credential={row} />,
      },
      {
        id: "test",
        header: "Last test",
        cell: (row) => (row.provider_id === COMPOSIO_PROVIDER_ID ? <ComposioTestStatus /> : <TestStatus credential={row} />),
      },
      {
        id: "actions",
        header: <span className="sr-only">Actions</span>,
        align: "end",
        interactive: true,
        cell: (row) => <CredentialRowActions credential={row} onOpenDialog={openDialog} onDelete={setDeleting} />,
      },
    ];

    body = search.noMatches ? (
      <ListNoMatches search={search} items="credentials" />
    ) : (
      <ResponsiveTable
        label="Credentials"
        columns={columns}
        rows={search.filtered}
        getRowKey={(row) => row.id}
        renderCard={(row) => (
          <div className="flex flex-col gap-2">
            <div className="flex items-start justify-between gap-2">
              <CredentialIdentity credential={row} spec={specs.get(row.provider_id)} registry={registry} query={query} />
              <CredentialRowActions credential={row} onOpenDialog={openDialog} onDelete={setDeleting} />
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-[2.125rem] text-label text-text-secondary">
              <CredentialKind spec={specs.get(row.provider_id)} registry={registry} />
              <Fingerprint value={row.fingerprint} />
              <UsageCell usage={usage.get(row.id) ?? NO_USAGE} />
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-[2.125rem] text-label text-text-secondary">
              <span>
                Added <RelativeTime iso={row.created_at} />
              </span>
              <LastUsed credential={row} withLabel />
            </div>
            <div className="pl-[2.125rem]">
              {row.provider_id === COMPOSIO_PROVIDER_ID ? <ComposioTestStatus /> : <TestStatus credential={row} />}
            </div>
          </div>
        )}
      />
    );
  }

  return (
    <div>
      <PageHeader
        back={{ href: "/console/providers", label: "Back to providers" }}
        title="Credentials"
        description="Vendor keys your agents and tools use: encrypted at rest, and only a fingerprint is ever shown."
        actions={rows.length > 0 ? addButton : undefined}
      />
      {rows.length > 0 && !credentialsQuery.isLoading && !credentialsQuery.isError ? (
        <ListSearchField search={search} label="Search credentials" total={rows.length} />
      ) : null}
      {body}
      <CredentialDialog
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        mode={dialog?.mode ?? "create"}
        credential={dialog?.credential}
        spec={dialog?.credential ? specs.get(dialog.credential.provider_id) : undefined}
      />
      <DeleteCredentialDialog
        credential={deleting}
        usage={deleting ? (usage.get(deleting.id) ?? NO_USAGE) : NO_USAGE}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}

/**
 * "OpenRouter", not "OpenRouter (LLM)" (R-V4-7's follow-up, `credentialDisplay`
 * in `provider-meta.ts`): a key stored under a credential home reads as a
 * vendor-level key everywhere — the narrower kind-scoped label made it look
 * like the key only worked for that one kind. The "used by" kinds themselves
 * live in the "Kind" column (`CredentialKind` below), not here.
 */
function CredentialIdentity({
  credential,
  spec,
  registry,
  query = "",
}: {
  credential: CredentialOut;
  spec: ProviderSpec | undefined;
  registry: ProviderSpec[];
  query?: string;
}) {
  const title = spec ? credentialDisplay(spec, registry).title : credential.provider_id;
  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <VendorMark vendor={spec?.vendor ?? credential.provider_id} size="md" className="mt-0.5" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-foreground">
          <Highlight text={credential.label} query={query} />
        </span>
        <span className="truncate text-caption text-text-secondary">
          <Highlight text={title} query={query} />
        </span>
      </div>
    </div>
  );
}

/**
 * The "Kind" column / mobile-card kind line. A shared credential home (e.g.
 * the OpenRouter key) shows a chip per kind it covers instead of the single
 * kind its own registry entry happens to carry — that single-kind label is
 * exactly what made the key look LLM-only.
 */
function CredentialKind({ spec, registry }: { spec: ProviderSpec | undefined; registry: ProviderSpec[] }) {
  if (!spec) return <span className="text-label text-text-secondary">—</span>;
  const { usedBy } = credentialDisplay(spec, registry);
  if (usedBy.length <= 1) {
    return <span className="text-label text-text-secondary">{KIND_LABEL[spec.kind]}</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1" aria-label={`Used by ${usedBy.join(", ")}`}>
      {usedBy.map((kind) => (
        <StatusPill key={kind} tone="neutral" size="sm">
          {kind}
        </StatusPill>
      ))}
    </div>
  );
}

/** Fingerprints are not secrets (§4.5), so they are copyable. */
function Fingerprint({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <span className="font-mono text-label text-text-secondary tabular-nums">{value}</span>
      <CopyButton value={value} label="Copy fingerprint" size="xs" />
    </span>
  );
}

function UsageCell({ usage }: { usage: CredentialUsage }) {
  const text = usageText(usage);
  if (usage.agents.length === 0 && usage.tools.length === 0) {
    return <span className="text-label text-text-secondary">{text}</span>;
  }
  const names = [...usage.agents.map((a) => a.name), ...usage.tools.map((t) => t.name)].join(", ");
  return (
    <Link
      href="/console/agents"
      title={names}
      className="rounded-sm text-label text-foreground underline-offset-3 hover:underline"
    >
      {text}
    </Link>
  );
}

/**
 * "Last used" (V6-32, `CredentialOut.last_used_at`): when a call, a tool, a catalog read or an
 * embed last used the key. `withLabel` spells the column name out for the mobile card.
 */
function LastUsed({ credential, withLabel = false }: { credential: CredentialOut; withLabel?: boolean }) {
  if (!credential.last_used_at) {
    return <span className="text-label text-text-secondary">Not used yet</span>;
  }
  return (
    <span className="text-label text-text-secondary">
      {withLabel ? "Last used " : null}
      <RelativeTime iso={credential.last_used_at} />
    </span>
  );
}

/** The first sentence of a vendor message, short enough for a table cell (the full text is the tooltip). */
export function shortTestMessage(message: string | null | undefined, max = 60): string {
  const text = (message ?? "").trim().split(/(?<=\.)\s/)[0] ?? "";
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}

/**
 * The key's recorded test (V6-32): "Works · tested 2 days ago", "Failed · 29 Sep · <reason>",
 * "No automatic test for this provider" or "Not tested yet". The api records every test
 * (including the one run while adding the key) on the key itself, so this survives a reload;
 * `useCredentialTest` only adds the live "Testing…" state and the 10 s result chip.
 */
export function RecordedTest({ credential }: { credential: Pick<CredentialOut, "last_test_at" | "last_test_ok" | "last_test_message"> }) {
  const testedAt = credential.last_test_at;
  if (!testedAt) return <span className="text-label text-text-secondary">Not tested yet</span>;
  if (credential.last_test_ok === true) {
    return (
      <span className="text-label text-text-secondary" title={credential.last_test_message ?? undefined}>
        <span className="font-medium text-success-text">Works</span> · tested <RelativeTime iso={testedAt} />
      </span>
    );
  }
  if (credential.last_test_ok === false) {
    const reason = shortTestMessage(credential.last_test_message);
    return (
      <span className="text-label text-text-secondary" title={credential.last_test_message ?? undefined}>
        <span className="font-medium text-destructive-text">Failed</span> · <RelativeTime iso={testedAt} />
        {reason ? ` · ${reason}` : null}
      </span>
    );
  }
  return (
    <span className="text-label text-text-secondary" title={credential.last_test_message ?? undefined}>
      {OUTCOME_LABEL["not-implemented"]}
    </span>
  );
}

/** Result chip for 10 s after a test, then the key's recorded test (`RecordedTest`). */
function TestStatus({ credential }: { credential: CredentialOut }) {
  const { last, pending } = useCredentialTest(credential.id);
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
        <span className="text-label text-text-secondary">Testing…</span>
      ) : last && fresh ? (
        <StatusPill tone={OUTCOME_TONE[last.outcome]} size="sm" className="max-w-64">
          <span className="truncate" title={last.message}>
            {last.outcome === "passed" || last.outcome === "timed-out" ? OUTCOME_LABEL[last.outcome] : last.message || OUTCOME_LABEL[last.outcome]}
          </span>
        </StatusPill>
      ) : last && !credential.last_test_at ? (
        // The api has not answered with the recorded result yet (or the test timed out here).
        <span className="text-label text-text-secondary" title={last.message}>
          {OUTCOME_LABEL[last.outcome]} · <RelativeTime iso={last.testedAt} />
        </span>
      ) : (
        <RecordedTest credential={credential} />
      )}
    </span>
  );
}

/**
 * The Composio row's status cell: the same persisted chip Tools -> Apps'
 * header shows (`useComposioStatus`, docs/v5/COMPOSIO.md §6, D-V5-C13) — one
 * source of truth, so the two screens can never disagree.
 */
function ComposioTestStatus() {
  const { status } = useComposioStatus();
  const chip = composioStatusChip(status);
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <StatusPill tone={chip.tone} size="sm">
        {chip.label}
      </StatusPill>
      {status?.last_test_at ? <RelativeTime iso={status.last_test_at} className="text-label text-text-secondary" /> : null}
    </span>
  );
}

/** Dispatches to the Composio-specific menu (docs/v5/COMPOSIO.md §6, D-V5-C13) or the generic one. */
function CredentialRowActions(props: {
  credential: CredentialOut;
  onOpenDialog: (mode: CredentialDialogMode, credential: CredentialOut) => void;
  onDelete: (credential: CredentialOut) => void;
}) {
  if (props.credential.provider_id === COMPOSIO_PROVIDER_ID) {
    return <ComposioRowActions credential={props.credential} onDelete={props.onDelete} />;
  }
  return <RowActions {...props} />;
}

/**
 * The Composio row's menu: **Validate** (the same stored-key test, through
 * the shared `useComposioStatus` hook so the header chip here and on Tools ->
 * Apps update together), **Rotate** (`EnableComposioDialog` in `rotate`
 * mode — a new key on this same credential id), **Disable** / **Enable**
 * (the workspace's Apps toggle — confirmed, since it pauses every tool that
 * uses a connected app) and **Remove key** (the generic delete flow,
 * renamed: this is the one destructive, no-way-back action).
 */
function ComposioRowActions({ credential, onDelete }: { credential: CredentialOut; onDelete: (credential: CredentialOut) => void }) {
  const { status, validate, validating } = useComposioStatus();
  const enableApps = useEnableApps();
  const disableApps = useDisableApps();
  const { can, isLoading } = useCan("admin");
  const [rotateOpen, setRotateOpen] = React.useState(false);
  const [disableConfirmOpen, setDisableConfirmOpen] = React.useState(false);

  async function turnOn() {
    try {
      await enableApps.mutateAsync();
      toast.success("Apps turned on");
    } catch (error) {
      toast.error(appsErrorMessage(error));
    }
  }

  // Admin actions show disabled while the role loads, then only for admins (D12).
  const admin = can || isLoading;
  const actions: RowMenuAction[] = [
    { label: "Validate", icon: FlaskConicalIcon, disabled: validating, onSelect: () => void validate() },
  ];
  if (admin) {
    actions.push({ label: "Rotate", icon: RefreshCwIcon, disabled: isLoading, onSelect: () => setRotateOpen(true) });
    actions.push({
      label: status?.enabled ? "Disable" : "Enable",
      icon: PlugIcon,
      disabled: isLoading,
      onSelect: () => (status?.enabled ? setDisableConfirmOpen(true) : void turnOn()),
    });
  }

  return (
    <>
      <RowMenu
        label={`Actions for ${credential.label}`}
        size="sm"
        actions={actions}
        destructive={admin ? { label: "Remove key", icon: Trash2Icon, disabled: isLoading, onSelect: () => onDelete(credential) } : undefined}
      />
      {can ? (
        <>
          <EnableComposioDialog mode="rotate" credentialId={credential.id} open={rotateOpen} onOpenChange={setRotateOpen} />
          <ConfirmDialog
            open={disableConfirmOpen}
            onOpenChange={setDisableConfirmOpen}
            title="Turn off Apps?"
            description="The key and every connection are kept. Tools that use them switch off until you turn Apps back on."
            confirmLabel="Turn off"
            busyLabel="Turning off…"
            onConfirm={async () => {
              try {
                await disableApps.mutateAsync();
              } catch (error) {
                // The dialog stays open and says why, in the Apps screen's own words.
                throw new Error(appsErrorMessage(error));
              }
              toast.success("Apps turned off");
            }}
          />
        </>
      ) : null}
    </>
  );
}

function RowActions({
  credential,
  onOpenDialog,
  onDelete,
}: {
  credential: CredentialOut;
  onOpenDialog: (mode: CredentialDialogMode, credential: CredentialOut) => void;
  onDelete: (credential: CredentialOut) => void;
}) {
  const { run, pending } = useCredentialTest(credential.id);
  // Testing, rotating, renaming and deleting a key are all admin writes
  // server-side. They show disabled while the role loads, then only for admins
  // (D12); with nothing left to offer, the menu hides itself.
  const { can, isLoading } = useCan("admin");
  if (!can && !isLoading) return null;
  const actions: RowMenuAction[] = [
    { label: "Test", icon: FlaskConicalIcon, disabled: isLoading || pending, onSelect: () => void run() },
    { label: "Rotate", icon: RefreshCwIcon, disabled: isLoading, onSelect: () => onOpenDialog("rotate", credential) },
    { label: "Rename", icon: PencilIcon, disabled: isLoading, onSelect: () => onOpenDialog("rename", credential) },
  ];
  return (
    <RowMenu
      label={`Actions for ${credential.label}`}
      size="sm"
      actions={actions}
      destructive={{ label: "Delete", icon: Trash2Icon, disabled: isLoading, onSelect: () => onDelete(credential) }}
    />
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
  const [failure, setFailure] = React.useState<string | null>(null);

  React.useEffect(() => {
    setConflict(null);
    setFailure(null);
  }, [credential]);

  async function confirm() {
    if (!credential) return;
    setFailure(null);
    try {
      await deleteMutation.mutateAsync(credential.id);
      toast.success("Credential deleted");
      onClose();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(error.message);
        return;
      }
      // Stays open and says why (spec section 9); nothing was deleted.
      setFailure(errorMessage(error));
    }
  }

  const inUse = usage.agents.length > 0 || usage.tools.length > 0;

  return (
    <Dialog open={credential !== null} onOpenChange={(open) => (open || deleteMutation.isPending ? undefined : onClose())}>
      <DialogContent role="alertdialog" className="sm:w-[min(calc(100vw-32px),440px)]" onInteractOutside={(event) => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Delete {credential?.label}</DialogTitle>
          <DialogDescription>
            The key is removed from the vault. Agents and tools that use it stop working until they get another key.
          </DialogDescription>
        </DialogHeader>
        {conflict ? (
          <div
            role="alert"
            className="flex flex-col gap-1.5 rounded border border-destructive-border bg-destructive-subtle px-3 py-2.5 text-label text-destructive-text"
          >
            {usage.agents.length > 0 ? (
              <p>
                Used by{" "}
                {usage.agents.map((agent, index) => (
                  <React.Fragment key={agent.id}>
                    {index > 0 ? ", " : null}
                    <Link
                      href={`/console/agents/${agent.id}?section=providers`}
                      className="font-medium underline underline-offset-3"
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
          <p className="text-label text-warning-text">{usageText(usage)}.</p>
        ) : null}
        <FormError>{failure}</FormError>
        <DialogFooter>
          <Button type="button" onClick={onClose} disabled={deleteMutation.isPending}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            busy={deleteMutation.isPending}
            busyLabel="Deleting…"
            onClick={() => void confirm()}
          >
            Delete credential
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
