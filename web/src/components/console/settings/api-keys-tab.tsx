"use client";

import * as React from "react";
import { KeyRoundIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { Section, SectionRow } from "@/components/shared/section";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { api, ApiError } from "@/lib/api";
import type { ApiKeyCreated, ApiKeyOut, Scope } from "./api-types";
import { SCOPES } from "./api-types";
import { useActiveWorkspace, useApiKeys, useInvalidateSettings } from "./use-settings-queries";
import { SkeletonRows } from "@/components/shared/loading-state";

function keyStatus(key: ApiKeyOut): { tone: "success" | "danger" | "warning"; label: string } {
  if (key.revoked_at) return { tone: "danger", label: "Revoked" };
  if (key.expires_at && new Date(key.expires_at).getTime() < Date.now()) return { tone: "warning", label: "Expired" };
  return { tone: "success", label: "Active" };
}

export function ApiKeysTab() {
  const { workspace: membership } = useActiveWorkspace();
  const keysQuery = useApiKeys(membership?.id);
  const invalidate = useInvalidateSettings();
  const keys = keysQuery.data?.items ?? [];

  const columns: ResponsiveTableColumn<ApiKeyOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (key) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{key.name}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{key.prefix}…</div>
        </div>
      ),
    },
    {
      id: "scopes",
      header: "Scopes",
      cell: (key) => <span className="text-xs text-muted-foreground">{key.scopes.join(", ")}</span>,
    },
    {
      id: "status",
      header: "Status",
      cell: (key) => {
        const status = keyStatus(key);
        return (
          <StatusChip tone={status.tone} size="sm">
            {status.label}
          </StatusChip>
        );
      },
    },
    {
      id: "last_used",
      header: "Last used",
      cell: (key) => (key.last_used_at ? <RelativeTime iso={key.last_used_at} /> : <span className="text-muted-foreground">Never</span>),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (key) =>
        key.revoked_at ? null : (
          <RevokeButton apiKey={key} onRevoked={() => invalidate(membership?.id)} />
        ),
    },
  ];

  return (
    <Section
      id="api-keys"
      title="API keys"
      description="Keys authenticate scripts and integrations as this workspace."
      aside={membership ? <CreateKeyDialog onCreated={() => invalidate(membership.id)} /> : null}
    >
      <SectionRow>
        {keysQuery.isLoading ? (
          <SkeletonRows label="Loading API keys" rowClassName="h-12" />
        ) : keysQuery.isError ? (
          <ErrorBanner message={`Couldn't load API keys — ${errorMessage(keysQuery.error)}`} onRetry={() => keysQuery.refetch()} />
        ) : keys.length === 0 ? (
          <EmptyState icon={KeyRoundIcon} title="No API keys yet" compact />
        ) : (
          <ResponsiveTable<ApiKeyOut>
            columns={columns}
            rows={keys}
            label="API keys"
            getRowKey={(key) => key.id}
            renderCard={(key) => {
              const status = keyStatus(key);
              return (
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium text-foreground">{key.name}</div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{key.prefix}…</div>
                  </div>
                  <StatusChip tone={status.tone} size="sm">
                    {status.label}
                  </StatusChip>
                </div>
              );
            }}
          />
        )}
      </SectionRow>
    </Section>
  );
}

function RevokeButton({ apiKey, onRevoked }: { apiKey: ApiKeyOut; onRevoked: () => void }) {
  const [busy, setBusy] = React.useState(false);

  async function onClick() {
    setBusy(true);
    try {
      await api.delete(`api-keys/${apiKey.id}`);
      toast.success(`Revoked "${apiKey.name}"`);
      onRevoked();
    } catch (err) {
      toast.error(`Couldn't revoke "${apiKey.name}" — ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} disabled={busy}>
      Revoke
    </Button>
  );
}

function CreateKeyDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState<Set<Scope>>(new Set());
  const [creating, setCreating] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);

  function reset() {
    setName("");
    setScopes(new Set());
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
      setError("Choose at least one scope.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const key = await api.post<ApiKeyCreated>("api-keys", { name, scopes: [...scopes] });
      setCreated(key);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : errorMessage(err));
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
        <Button type="button" size="sm">
          <PlusIcon />
          Create key
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create an API key</DialogTitle>
          <DialogDescription>The raw key is shown once, right after creation — copy it now.</DialogDescription>
        </DialogHeader>
        {created ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 p-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs">{created.key}</code>
              <CopyButton value={created.key} label="Copy API key" />
            </div>
            <p className="text-xs text-muted-foreground">This key won&apos;t be shown again.</p>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            {error ? <ErrorBanner message={error} /> : null}
            <Field label="Name" htmlFor="api-key-name" required hint="What is this key for?">
              <Input id="api-key-name" required value={name} onChange={(event) => setName(event.target.value)} disabled={creating} />
            </Field>
            <div className="space-y-2">
              <Label>Scopes</Label>
              <div className="grid grid-cols-2 gap-2">
                {SCOPES.map((scope) => (
                  <label key={scope} className="flex items-center gap-2 text-sm">
                    <Checkbox checked={scopes.has(scope)} onCheckedChange={() => toggleScope(scope)} disabled={creating} />
                    <span className="font-mono text-xs">{scope}</span>
                  </label>
                ))}
              </div>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={creating}>
                Create
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
