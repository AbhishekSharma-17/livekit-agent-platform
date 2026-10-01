"use client";

import * as React from "react";
import { toast } from "sonner";

import { TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { useDeleteConnection } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * Deleting a connection (docs/ui/DESIGN-SYSTEM.md section 9: typed
 * confirmation for irreversible, data-destroying actions). The stored LiveKit
 * key and secret go with it, so the person types the connection's name. The
 * api refuses while agents are bound to it or while it is the default; the
 * dialog says so up front and, if the api still refuses, stays open with the
 * reason in plain words.
 */
export function DeleteConnectionDialog({
  connection,
  open,
  onOpenChange,
  trigger,
  onDeleted,
}: {
  connection: ConnectionOut;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
  onDeleted?: () => void;
}) {
  const deleteConnection = useDeleteConnection();
  return (
    <TypedConfirmDialog
      trigger={trigger}
      open={open}
      onOpenChange={onOpenChange}
      title={`Delete “${connection.name}”?`}
      description={
        <>
          This can&apos;t be undone. Type the connection&apos;s name,{" "}
          <strong className="font-mono">{connection.name}</strong>, to confirm.
        </>
      }
      confirmText={connection.name}
      inputLabel="Connection name"
      confirmLabel="Delete connection"
      onConfirm={async () => {
        await deleteConnection.mutateAsync(connection.id);
        toast.success(`${connection.name} deleted.`);
        onDeleted?.();
      }}
    >
      <ul className="list-disc pl-5">
        <li>The stored LiveKit API key and secret</li>
        <li>The connection&apos;s settings and its worker settings</li>
      </ul>
      <p>
        Agents bound to this connection block the delete. Move them to another connection first.
        {connection.is_default ? " The default connection can't be deleted either. Make another one the default first." : null}
      </p>
    </TypedConfirmDialog>
  );
}
