"use client";

import * as React from "react";
import { KeyRoundIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { CheckboxRow } from "@/components/shared/choice";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field, FormError } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { RequireWrite } from "@/components/shared/require-write";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus, type LifecycleStatus } from "@/components/shared/status-map";
import { Tag } from "@/components/shared/tag";
import { Section, SectionRow } from "@/components/shared/section";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { api } from "@/lib/api";
import type { ApiKeyCreated, ApiKeyOut, Scope } from "./api-types";
import { SCOPE_LABEL, SCOPES } from "./api-types";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "./list-search";
import { RowsSkeleton } from "./settings-card";
import { useActiveWorkspace, useApiKeys, useInvalidateSettings } from "./use-settings-queries";

/** Shared with `agent-keys-table.tsx` (v3): a key's status, tone from the shared lifecycle map. */
export function keyStatus(key: ApiKeyOut): LifecycleStatus {
  if (key.revoked_at) return lifecycleStatus("revoked");
  if (key.expires_at && new Date(key.expires_at).getTime() < Date.now()) return lifecycleStatus("expired");
  return { tone: lifecycleStatus("ready").tone, label: "Active" };
}

export function KeyStatus({ apiKey }: { apiKey: ApiKeyOut }) {
  const status = keyStatus(apiKey);
  return (
    <StatusPill tone={status.tone} size="sm">
      {status.label}
    </StatusPill>
  );
}

/**
 * Revoke behind a confirm dialog, shared by the plain and agent keys tables.
 * A failure throws inside `onConfirm`, so the dialog stays open and says why.
 * Callers render it only for people who can revoke (admins, D12).
 */
export function RevokeKeyButton({
  apiKey,
  onRevoked,
  description = "Anything using this key stops working right away. Revoking can't be undone.",
  confirmLabel = "Revoke key",
}: {
  apiKey: ApiKeyOut;
  onRevoked: () => void;
  description?: string;
  confirmLabel?: string;
}) {
  return (
    <ConfirmDialog
      trigger={
        <Button type="button" variant="ghost" size="sm">
          Revoke
        </Button>
      }
      title={`Revoke "${apiKey.name}"?`}
      description={description}
      confirmLabel={confirmLabel}
      busyLabel="Revoking…"
      onConfirm={async () => {
        await api.delete(`api-keys/${apiKey.id}`);
        toast.success(`Revoked "${apiKey.name}"`);
        onRevoked();
      }}
    />
  );
}

/**
 * `GET /v1/api-keys` needs `admin` server-side for reads as well as writes
 * (`routers/api_keys.py::KeyAdminDep`), so the whole tab is gated like the
 * Webhooks and AI agents tabs: a clear locked state instead of a query that
 * only ever 403s.
 */
export function ApiKeysTab() {
  return (
    <RequireWrite min="admin" title="Only admins and owners can see API keys">
      <ApiKeysTabInner />
    </RequireWrite>
  );
}

function ApiKeysTabInner() {
  const { workspace: membership } = useActiveWorkspace();
  const keysQuery = useApiKeys(membership?.id);
  const invalidate = useInvalidateSettings();
  const keys = React.useMemo(() => keysQuery.data?.items ?? [], [keysQuery.data]);
  const search = useListSearch("api-keys", keys, (key) => [key.name, key.prefix, ...key.scopes]);
  const query = search.query;

  const identity = (key: ApiKeyOut) => (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5">
        <span className="truncate font-medium text-foreground">
          <Highlight text={key.name} query={query} />
        </span>
        {key.kind === "agent" ? <Tag>Agent</Tag> : null}
      </div>
      <div className="truncate font-mono text-caption text-text-secondary">{key.prefix}…</div>
    </div>
  );

  const revoke = (key: ApiKeyOut) =>
    key.revoked_at ? null : <RevokeKeyButton apiKey={key} onRevoked={() => invalidate(membership?.id)} />;

  const columns: ResponsiveTableColumn<ApiKeyOut>[] = [
    { id: "name", header: "Name", cell: identity },
    {
      id: "scopes",
      header: "Scopes",
      // Scope lists run long (an agent key has a dozen): wrap instead of widening the page.
      className: "min-w-48 whitespace-normal",
      cell: (key) => <span className="text-caption text-text-secondary">{key.scopes.join(", ")}</span>,
    },
    { id: "status", header: "Status", cell: (key) => <KeyStatus apiKey={key} /> },
    {
      id: "last_used",
      header: "Last used",
      cell: (key) =>
        key.last_used_at ? <RelativeTime iso={key.last_used_at} /> : <span className="text-text-secondary">Never</span>,
    },
    { id: "actions", header: <span className="sr-only">Actions</span>, align: "end", interactive: true, cell: revoke },
  ];

  return (
    <Section
      id="api-keys"
      title="API keys"
      description="Keys authenticate scripts and integrations as this workspace."
      aside={membership ? <CreateKeyDialog onCreated={() => invalidate(membership.id)} /> : null}
    >
      <SectionRow>
        {keysQuery.isLoading || !membership ? (
          <RowsSkeleton label="Loading API keys" />
        ) : keysQuery.isError ? (
          <ErrorBanner error={keysQuery.error} context={{ action: "load API keys" }} onRetry={() => void keysQuery.refetch()} />
        ) : keys.length === 0 ? (
          <EmptyState
            variant="plain"
            icon={KeyRoundIcon}
            title="No API keys yet"
            description="Create a key to call the API from a script or integration."
          />
        ) : (
          <>
            <ListSearchField search={search} label="Search API keys" total={keys.length} />
            {search.noMatches ? (
              <ListNoMatches search={search} items="keys" />
            ) : (
              <ResponsiveTable<ApiKeyOut>
                columns={columns}
                rows={search.filtered}
                label="API keys"
                getRowKey={(key) => key.id}
                renderCard={(key) => (
                  <div className="flex items-center justify-between gap-2">
                    {identity(key)}
                    <div className="flex shrink-0 items-center gap-1">
                      <KeyStatus apiKey={key} />
                      {revoke(key)}
                    </div>
                  </div>
                )}
              />
            )}
          </>
        )}
      </SectionRow>
    </Section>
  );
}

function CreateKeyDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState<Set<Scope>>(new Set());
  const [creating, setCreating] = React.useState(false);
  const [scopeError, setScopeError] = React.useState<string | null>(null);
  const [error, setError] = React.useState<unknown>(null);
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);

  function reset() {
    setName("");
    setScopes(new Set());
    setScopeError(null);
    setError(null);
    setCreated(null);
  }

  function toggleScope(scope: Scope) {
    setScopes((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (scopes.size === 0) {
      setScopeError("Choose at least one scope.");
      return;
    }
    setCreating(true);
    setScopeError(null);
    setError(null);
    try {
      const key = await api.post<ApiKeyCreated>("api-keys", { name, scopes: [...scopes] });
      setCreated(key);
      onCreated();
    } catch (err) {
      setError(err);
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="primary" size="sm">
          <PlusIcon aria-hidden="true" />
          Create key
        </Button>
      </DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Create an API key</DialogTitle>
          <DialogDescription>The raw key is shown once, right after creation. Copy it now.</DialogDescription>
        </DialogHeader>
        {created ? (
          <>
            <DialogBody className="gap-3">
              <div className="flex items-center gap-2 rounded border border-border bg-muted p-2">
                <code className="min-w-0 flex-1 font-mono text-caption break-all">{created.key}</code>
                <CopyButton value={created.key} label="Copy API key" />
              </div>
              <p className="text-caption text-text-secondary">This key won&apos;t be shown again.</p>
            </DialogBody>
            <DialogFooter showCloseButton />
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
            <DialogBody className="gap-4">
              {error ? <ErrorBanner error={error} context={{ action: "create the key" }} /> : null}
              <Field label="Name" htmlFor="api-key-name" hint="What is this key for?">
                <Input id="api-key-name" required value={name} onChange={(event) => setName(event.target.value)} disabled={creating} />
              </Field>
              <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
                <legend className="mb-1.5 text-label font-medium text-foreground">Scopes</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {SCOPES.map((scope) => (
                    <CheckboxRow
                      key={scope}
                      checked={scopes.has(scope)}
                      onChange={() => toggleScope(scope)}
                      disabled={creating}
                      label={
                        <span className="flex flex-wrap items-baseline gap-x-1.5">
                          <span className="font-mono text-caption">{scope}</span>
                          {SCOPE_LABEL[scope] ? (
                            <span className="text-caption text-text-secondary">({SCOPE_LABEL[scope]})</span>
                          ) : null}
                        </span>
                      }
                    />
                  ))}
                </div>
                <FormError>{scopeError}</FormError>
              </fieldset>
            </DialogBody>
            <DialogFooter>
              <Button type="button" onClick={() => setOpen(false)} disabled={creating}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" busy={creating} busyLabel="Creating…">
                Create
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

