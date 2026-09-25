"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import {
  useDisconnectApp,
  useReconnectApp,
  useToolProviderConnection,
  useToolProviderToolkit,
} from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import { fieldsFor } from "@/components/console/tools/apps/connect-app-dialog";
import { ActionsDialog } from "@/components/console/tools/apps/actions-dialog";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { pluralize } from "@/lib/format";
import type { ConnectionStatus } from "@/components/console/tools/apps/types";
import type { AppAuthField, ToolkitOut } from "@/contracts/lkap-contracts";

const STATUS_TONE: Record<ConnectionStatus, StatusTone> = {
  active: "success",
  initiated: "info",
  expired: "warning",
  failed: "danger",
  inactive: "neutral",
  unknown: "neutral",
};

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  active: "Connected",
  initiated: "Waiting for sign-in…",
  expired: "Needs reconnect",
  failed: "Needs reconnect",
  inactive: "Needs reconnect",
  unknown: "Unknown",
};

/**
 * One connected app's status row (docs/v5/COMPOSIO.md §6): a live status
 * chip (`useToolProviderConnection` polls while `initiated`), Reconnect
 * (a new sign-in link for OAuth methods, a small key form for `api_key`),
 * Disconnect (confirm, naming the picked actions that pause) and Actions.
 */
export function ConnectionRow({ connectionId, toolkit }: { connectionId: string; toolkit: Pick<ToolkitOut, "slug" | "name" | "auth_fields"> }) {
  const connectionQuery = useToolProviderConnection(connectionId, { poll: true });
  const reconnectMutation = useReconnectApp();
  const disconnectMutation = useDisconnectApp();
  const [keyDialogOpen, setKeyDialogOpen] = React.useState(false);
  const [actionsOpen, setActionsOpen] = React.useState(false);
  const [redirectUrl, setRedirectUrl] = React.useState<string | null>(null);
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();
  // `toolkit` here is the *list* read (`AppCard`'s `ToolkitOut`), whose
  // `auth_fields` is always `{}` (`toolkit_out(item, detail=False)`,
  // docs/v5/COMPOSIO.md §4) — the reconnect key form needs the *detail*
  // read's real fields, fetched only once the dialog actually opens.
  const detailQuery = useToolProviderToolkit(keyDialogOpen ? toolkit.slug : null);

  const connection = connectionQuery.data;
  if (!connection) {
    return <p className="text-[0.8125rem] text-muted-foreground">Loading connection…</p>;
  }

  const needsReconnect = connection.needs_reconnect || connection.status !== "active";
  const status: ConnectionStatus = connection.status;

  async function startReconnect() {
    if (connection!.method === "api_key") {
      setKeyDialogOpen(true);
      return;
    }
    try {
      const result = await reconnectMutation.mutateAsync({ id: connectionId });
      if (result.redirect_url) {
        window.open(result.redirect_url, "_blank", "noopener");
        setRedirectUrl(result.redirect_url);
      }
    } catch (error) {
      toast.error(`Couldn't reconnect — ${appsErrorMessage(error)}`);
    }
  }

  async function handleDisconnect() {
    try {
      await disconnectMutation.mutateAsync({ id: connectionId, purge: false });
      toast.success(`${toolkit.name} disconnected`);
    } catch (error) {
      toast.error(`Couldn't disconnect — ${appsErrorMessage(error)}`);
    }
  }

  const pickedCount = (connection.picked_actions ?? []).length;
  const disconnectDescription =
    pickedCount > 0
      ? `${pluralize(pickedCount, "action", "actions")} agents use from ${toolkit.name} will pause until it's reconnected.`
      : `Agents lose access to ${toolkit.name} until it's reconnected.`;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <StatusChip tone={STATUS_TONE[status]} dot size="sm">
            {STATUS_LABEL[status]}
          </StatusChip>
          {connection.last_checked_at ? (
            <span className="text-xs text-muted-foreground">
              Checked <RelativeTime iso={connection.last_checked_at} />
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {needsReconnect ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!canWrite || reconnectMutation.isPending}
              title={canWrite ? undefined : writeReason}
              onClick={() => void startReconnect()}
            >
              Reconnect
            </Button>
          ) : null}
          <Button type="button" variant="outline" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason} onClick={() => setActionsOpen(true)}>
            Actions
          </Button>
          <ConfirmDialog
            trigger={
              <Button type="button" variant="ghost" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
                Disconnect
              </Button>
            }
            title={`Disconnect ${toolkit.name}?`}
            description={disconnectDescription}
            confirmLabel="Disconnect"
            onConfirm={handleDisconnect}
          />
        </div>
      </div>

      {status === "initiated" && redirectUrl ? (
        <a href={redirectUrl} target="_blank" rel="noreferrer noopener" className="text-[0.8125rem] font-medium text-foreground underline underline-offset-2">
          Open sign-in page again
        </a>
      ) : null}

      <ReconnectKeyDialog
        open={keyDialogOpen}
        onOpenChange={setKeyDialogOpen}
        toolkitName={toolkit.name}
        fields={fieldsFor(detailQuery.data ?? toolkit, "api_key")}
        loading={detailQuery.isLoading}
        onSubmit={async (fields) => {
          await reconnectMutation.mutateAsync({ id: connectionId, fields });
          toast.success(`${toolkit.name} reconnected`);
          setKeyDialogOpen(false);
        }}
      />

      <ActionsDialog
        connectionId={connectionId}
        toolkitSlug={toolkit.slug}
        toolkitName={toolkit.name}
        pickedActions={connection.picked_actions ?? []}
        open={actionsOpen}
        onOpenChange={setActionsOpen}
      />
    </div>
  );
}

/** A small dialog for the one case Reconnect needs input: a new key on an `api_key` connection. */
function ReconnectKeyDialog({
  open,
  onOpenChange,
  toolkitName,
  fields,
  loading = false,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  toolkitName: string;
  fields: AppAuthField[];
  /** The detail fetch for `fields` hasn't resolved yet. */
  loading?: boolean;
  onSubmit: (fields: Record<string, string>) => Promise<void>;
}) {
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [pending, setPending] = React.useState(false);

  React.useEffect(() => {
    if (!open) setValues({});
  }, [open]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    setPending(true);
    try {
      await onSubmit(values);
      setValues({});
    } catch (error) {
      toast.error(`Couldn't reconnect — ${appsErrorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form onSubmit={(event) => void handleSubmit(event)} noValidate>
          <DialogHeader>
            <DialogTitle>Reconnect {toolkitName}</DialogTitle>
            <DialogDescription>Enter the new key. It is sent once and never stored in the console.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            {loading ? (
              <p className="text-[0.8125rem] text-muted-foreground">Loading…</p>
            ) : (
              fields.map((field) => (
                <Field key={field.name} label={field.label} htmlFor={`reconnect-field-${field.name}`} required={field.required}>
                  <Input
                    id={`reconnect-field-${field.name}`}
                    type={field.secret ? "password" : "text"}
                    autoComplete="new-password"
                    value={values[field.name] ?? ""}
                    onChange={(event) => setValues((prev) => ({ ...prev, [field.name]: event.target.value }))}
                  />
                </Field>
              ))
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Reconnecting…" : "Reconnect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
