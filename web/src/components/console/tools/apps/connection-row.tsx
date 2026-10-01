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
import { LifecycleBadge } from "@/components/shared/status-chip";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import {
  useDisconnectApp,
  useReconnectApp,
  useToolProviderConnection,
  useToolProviderToolkit,
  useUpdateConnection,
} from "@/components/console/lib/api-hooks";
import { appsErrorToast } from "@/components/console/tools/apps/use-composio";
import { fieldsFor } from "@/components/console/tools/apps/connect-app-dialog";
import { ActionsDialog } from "@/components/console/tools/apps/actions-dialog";
import { RenameAccountDialog } from "@/components/console/tools/apps/rename-account-dialog";
import { AccountIdentityLine, accountName } from "@/components/console/tools/apps/account-identity";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { LoadingRow } from "@/components/shared/loading-state";
import { Tag } from "@/components/shared/tag";
import { pluralize } from "@/lib/format";
import type { ConnectionStatus } from "@/components/console/tools/apps/types";
import type { AppAuthField, ToolkitOut } from "@/contracts/lkap-contracts";

/**
 * A connection's state, read through the shared lifecycle map (tone) with the
 * Apps wording for the label: every broken state asks for the same next step.
 */
const STATUS_STATE: Record<ConnectionStatus, string> = {
  active: "connected",
  initiated: "connecting",
  expired: "needs_reauth",
  failed: "failed",
  inactive: "disabled",
  unknown: "unknown",
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
 * One connected account's status row (docs/v5/COMPOSIO.md §6, R-V5-13): its
 * label, a Default chip when it is the app's default account, a live status
 * chip (`useToolProviderConnection` polls while `initiated`), Rename, Make
 * default (hidden once it already is), Reconnect (a new sign-in link for
 * OAuth methods, a small key form for `api_key`), Disconnect (confirm,
 * naming the picked actions that pause) and Actions. Used both directly on
 * the app card (one account) and once per row inside its accounts dialog
 * (several).
 */
export function ConnectionRow({
  connectionId,
  toolkit,
  showAccountLabel = false,
}: {
  connectionId: string;
  toolkit: Pick<ToolkitOut, "slug" | "name" | "auth_fields">;
  /** True once the app has more than one account (`AppAccountsDialog` sets this) — labels the Actions dialog by account so a builder can tell which inbox they just changed. */
  showAccountLabel?: boolean;
}) {
  const connectionQuery = useToolProviderConnection(connectionId, { poll: true });
  const reconnectMutation = useReconnectApp();
  const disconnectMutation = useDisconnectApp();
  const updateMutation = useUpdateConnection();
  const [keyDialogOpen, setKeyDialogOpen] = React.useState(false);
  const [actionsOpen, setActionsOpen] = React.useState(false);
  const [redirectUrl, setRedirectUrl] = React.useState<string | null>(null);
  const gate = useWriteGate();
  // `toolkit` here is the *list* read (`AppCard`'s `ToolkitOut`), whose
  // `auth_fields` is always `{}` (`toolkit_out(item, detail=False)`,
  // docs/v5/COMPOSIO.md §4) — the reconnect key form needs the *detail*
  // read's real fields, fetched only once the dialog actually opens.
  const detailQuery = useToolProviderToolkit(keyDialogOpen ? toolkit.slug : null);

  const connection = connectionQuery.data;
  if (!connection) {
    return <LoadingRow label="Loading connection…" className="rounded border border-border px-3" />;
  }

  const needsReconnect = connection.needs_reconnect || connection.status !== "active";
  const status: ConnectionStatus = connection.status;
  // Backfilled to the app's name for a connection made before labels existed
  // (R-V5-13's lazy backfill happens server-side; this mirrors it here for
  // the rare cache moment where `label` hasn't landed yet).
  const label = connection.label || toolkit.name;

  async function makeDefault() {
    try {
      await updateMutation.mutateAsync({ id: connectionId, body: { is_default: true } });
      toast.success(`${label} is now ${toolkit.name}'s default account`);
    } catch (error) {
      appsErrorToast("set the default", error);
    }
  }

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
      appsErrorToast("reconnect", error);
    }
  }

  async function handleDisconnect() {
    try {
      await disconnectMutation.mutateAsync({ id: connectionId, purge: false });
      toast.success(`${toolkit.name} disconnected`);
    } catch (error) {
      appsErrorToast("disconnect", error);
    }
  }

  const pickedCount = (connection.picked_actions ?? []).length;
  const disconnectDescription =
    pickedCount > 0
      ? `${pluralize(pickedCount, "action", "actions")} agents use from ${toolkit.name} will pause until it's reconnected.`
      : `Agents lose access to ${toolkit.name} until it's reconnected.`;

  return (
    <div className="flex flex-col gap-2 rounded border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          {showAccountLabel ? <VendorMark vendor={toolkit.name} size="sm" className="mt-0.5" /> : null}
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-body font-medium text-foreground">{label}</span>
              {connection.is_default ? <Tag>Default</Tag> : null}
              <LifecycleBadge state={STATUS_STATE[status]} label={STATUS_LABEL[status]} size="sm" />
              {connection.last_checked_at ? (
                <span className="text-caption text-text-secondary">
                  Checked <RelativeTime iso={connection.last_checked_at} />
                </span>
              ) : null}
            </div>
            {status !== "initiated" ? <AccountIdentityLine connection={connection} checkable={status === "active"} /> : null}
          </div>
        </div>
        {gate.show ? (
          <div className="flex flex-wrap items-center gap-1">
            <RenameAccountDialog
              connectionId={connectionId}
              currentLabel={label}
              toolkitName={toolkit.name}
              trigger={
                <Button type="button" variant="ghost" size="sm" disabled={gate.pending}>
                  Rename
                </Button>
              }
            />
            {!connection.is_default ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={gate.pending || updateMutation.isPending}
                onClick={() => void makeDefault()}
              >
                Make default
              </Button>
            ) : null}
            {needsReconnect ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={gate.pending || reconnectMutation.isPending}
                onClick={() => void startReconnect()}
              >
                Reconnect
              </Button>
            ) : null}
            <Button type="button" variant="secondary" size="sm" disabled={gate.pending} onClick={() => setActionsOpen(true)}>
              Actions
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" variant="ghost" size="sm" disabled={gate.pending}>
                  Disconnect
                </Button>
              }
              title={`Disconnect ${toolkit.name}?`}
              description={disconnectDescription}
              confirmLabel="Disconnect"
              onConfirm={handleDisconnect}
            />
          </div>
        ) : null}
      </div>

      {status === "initiated" && redirectUrl ? (
        <Button asChild variant="link" className="w-fit">
          <a href={redirectUrl} target="_blank" rel="noreferrer noopener">
            Open sign-in page again
          </a>
        </Button>
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
        accountLabel={showAccountLabel ? accountName(connection, toolkit.name) : undefined}
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
      appsErrorToast("reconnect", error);
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
              <LoadingRow label="Loading the key fields…" />
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
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" busy={pending} busyLabel="Reconnecting…">
              Reconnect
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
