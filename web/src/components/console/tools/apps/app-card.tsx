"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { ConnectAppDialog } from "@/components/console/tools/apps/connect-app-dialog";
import { ConnectionRow } from "@/components/console/tools/apps/connection-row";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { ToolkitOut } from "@/contracts/lkap-contracts";

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
 * One app in the gallery (docs/v5/COMPOSIO.md §6): logo, name, category
 * chips and either a Connect button or, once connected, its live
 * `ConnectionRow` (status, Reconnect, Disconnect, Actions).
 */
export function AppCard({ toolkit }: { toolkit: ToolkitOut }) {
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <AppLogo toolkit={toolkit} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-sm font-medium text-foreground">{toolkit.name}</span>
            {toolkit.connected ? (
              <StatusChip tone="success" size="sm">
                Connected
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
        <ConnectionRow connectionId={toolkit.connection_id} toolkit={toolkit} />
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
    </div>
  );
}
