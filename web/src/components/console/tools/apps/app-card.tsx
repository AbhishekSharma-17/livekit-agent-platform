"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { ConnectAppDialog } from "@/components/console/tools/apps/connect-app-dialog";
import { ConnectionRow } from "@/components/console/tools/apps/connection-row";
import { useToolProviderConnections } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { pluralize } from "@/lib/format";
import type { AppConnectionOut, ToolkitOut } from "@/contracts/lkap-contracts";

/** A toolkit's logo, or its vendor monogram when there is none (or it fails to load). */
function AppLogo({ toolkit }: { toolkit: ToolkitOut }) {
  const [failed, setFailed] = React.useState(false);
  if (!toolkit.logo || failed) return <VendorMark vendor={toolkit.name} size="lg" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={toolkit.logo}
      alt=""
      width={32}
      height={32}
      loading="lazy"
      referrerPolicy="no-referrer"
      className="size-8 shrink-0 rounded-sm border border-border object-contain bg-card"
      onError={() => setFailed(true)}
    />
  );
}

/**
 * One app in the gallery (docs/v5/COMPOSIO.md §6, R-V5-13): logo, name,
 * category chips and either a Connect button or, once connected, its live
 * account(s) — a single `ConnectionRow` for one account, or an "N accounts"
 * chip plus a button that opens every account in a dialog — and "Add
 * another account" underneath.
 */
export function AppCard({ toolkit }: { toolkit: ToolkitOut }) {
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();
  const connectionsQuery = useToolProviderConnections();
  const accounts = React.useMemo(
    () => (connectionsQuery.data?.items ?? []).filter((connection) => connection.toolkit === toolkit.slug),
    [connectionsQuery.data, toolkit.slug],
  );
  const [accountsOpen, setAccountsOpen] = React.useState(false);
  const [addAccountOpen, setAddAccountOpen] = React.useState(false);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <AppLogo toolkit={toolkit} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-sm font-medium text-foreground">{toolkit.name}</span>
            {toolkit.connected ? (
              <StatusChip tone="success" size="sm">
                {accounts.length > 1 ? pluralize(accounts.length, "account", "accounts") : "Connected"}
              </StatusChip>
            ) : null}
          </div>
          {toolkit.description ? <p className="line-clamp-2 text-xs text-pretty text-muted-foreground">{toolkit.description}</p> : null}
          <div className="flex flex-wrap gap-1">
            {(toolkit.categories ?? []).slice(0, 3).map((category) => (
              <StatusChip key={category} tone="neutral" size="sm">
                {category}
              </StatusChip>
            ))}
          </div>
        </div>
      </div>

      {toolkit.connected && toolkit.connection_id ? (
        <div className="flex flex-col items-start gap-2">
          {accounts.length > 1 ? (
            <Button type="button" variant="outline" size="sm" onClick={() => setAccountsOpen(true)}>
              Manage {pluralize(accounts.length, "account", "accounts")}
            </Button>
          ) : (
            <ConnectionRow connectionId={toolkit.connection_id} toolkit={toolkit} />
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!canWrite}
            title={canWrite ? undefined : writeReason}
            onClick={() => setAddAccountOpen(true)}
          >
            Add another account
          </Button>
        </div>
      ) : (
        <ConnectAppDialog
          toolkit={toolkit}
          trigger={
            <Button type="button" variant="outline" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason} className="self-start">
              Connect
            </Button>
          }
        />
      )}

      {accounts.length > 1 ? <AppAccountsDialog open={accountsOpen} onOpenChange={setAccountsOpen} toolkit={toolkit} accounts={accounts} /> : null}
      <ConnectAppDialog toolkit={toolkit} open={addAccountOpen} onOpenChange={setAddAccountOpen} isAddingAccount />
    </div>
  );
}

/**
 * Every account of this app (R-V5-13, docs/v5/PLAN-V5.md V5-54 card): one
 * `ConnectionRow` per account (label, status, Default chip, Rename, Make
 * default, Reconnect, Disconnect, Actions), in a dialog rather than a side
 * drawer.
 */
function AppAccountsDialog({
  open,
  onOpenChange,
  toolkit,
  accounts,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  toolkit: Pick<ToolkitOut, "slug" | "name" | "auth_fields">;
  accounts: AppConnectionOut[];
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md" aria-describedby="app-accounts-description">
        <DialogHeader>
          <DialogTitle>{toolkit.name} accounts</DialogTitle>
          <DialogDescription id="app-accounts-description">
            Every account of {toolkit.name} connected to this workspace.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-2">
          {accounts.map((account) => (
            <ConnectionRow key={account.id} connectionId={account.id} toolkit={toolkit} showAccountLabel />
          ))}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
