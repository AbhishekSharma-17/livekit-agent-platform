"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { PlugIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingRegion } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useDisableApps, useEnableApps, useToolProviderCategories, useToolProviderToolkits } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { AppGallery } from "@/components/console/tools/apps/app-gallery";
import { EnableComposioDialog } from "@/components/console/tools/apps/enable-composio-dialog";
import { appsErrorMessage, composioStatusChip, useComposioStatus } from "@/components/console/tools/apps/use-composio";
import type { AppsStatusOut } from "@/contracts/lkap-contracts";

/**
 * Tools -> Apps (docs/v5/COMPOSIO.md §6, D-V5-C13). Four states from
 * `AppsStatusOut`: loading; no key yet (`credential_id === null`) shows the
 * **Enable Composio** empty state; a key that exists but is turned off shows
 * a plain "turn it back on" prompt (no key dialog — the key is already
 * there); enabled shows the header (chip, Validate, Rotate, Disable, "Also
 * in Keys") and the app gallery.
 */
export function AppsTab() {
  const { status, isLoading, isError, error, refetch, validate, validating } = useComposioStatus();
  // `AppGallery` used to mount only once `status` resolved to "enabled" — a
  // strict waterfall (the status round-trip, then the toolkits one, one
  // after another) that is exactly what a builder feels as "the first load
  // takes a bit". Firing `AppGallery`'s own default-view queries here, at
  // the top of the tab, starts them alongside `status` instead: they share
  // the exact query key `AppGallery` uses for its initial render (no
  // search, no category, not connected-only), so by the time the branch
  // below actually mounts the gallery the data is already in cache, or
  // close to it — the query is deduplicated, not doubled. `enabled` reads
  // `true` while `status` is still loading (a guess that pays off whenever
  // Apps *are* already on, the common repeat visit) and stops guessing once
  // `status` confirms there is no key or it's off, so a workspace that has
  // never turned Apps on doesn't get a doomed request on every visit.
  const speculative = status ? status.enabled && status.credential_id != null : true;
  useToolProviderToolkits({ limit: 24 }, { enabled: speculative });
  useToolProviderCategories({ enabled: speculative });
  // The dialog is mounted unconditionally, outside the state branches below:
  // completing Save calls `enable` (D-V5-C13), whose result flips
  // `status.credential_id`/`.enabled` and swaps the branch that renders — if
  // the dialog lived inside the "no key yet" branch's `EmptyState`, that
  // swap would unmount it out from under the "Key saved… Done" view before
  // the builder ever saw it.
  const [enableOpen, setEnableOpen] = React.useState(false);

  let body: React.ReactNode;
  if (isLoading) {
    body = (
      <LoadingRegion label="Loading Apps" className="flex flex-col gap-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-32 w-full" />
      </LoadingRegion>
    );
  } else if (isError) {
    body = <ErrorBanner message={`Couldn't load Apps — ${appsErrorMessage(error)}`} onRetry={() => void refetch()} />;
  } else if (!status || status.credential_id == null) {
    body = (
      <EmptyState
        icon={PlugIcon}
        title="Connect Composio to see your apps"
        description="Add your Composio API key once to browse and connect apps for every agent in this workspace."
        action={
          <Button type="button" onClick={() => setEnableOpen(true)}>
            Enable Composio
          </Button>
        }
      />
    );
  } else if (!status.enabled) {
    body = <AppsTurnedOff onEnabled={() => void refetch()} />;
  } else {
    body = (
      <div className="flex flex-col gap-5">
        <AppsHeader status={status} onValidate={validate} validating={validating} onChanged={() => void refetch()} />
        <AppGallery />
      </div>
    );
  }

  return (
    <>
      <EnableComposioDialog mode="enable" open={enableOpen} onOpenChange={setEnableOpen} onDone={() => void refetch()} />
      {body}
    </>
  );
}

function AppsTurnedOff({ onEnabled }: { onEnabled: () => void }) {
  const enableApps = useEnableApps();
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  async function turnOn() {
    try {
      await enableApps.mutateAsync();
      toast.success("Apps turned on");
      onEnabled();
    } catch (error) {
      toast.error(`Couldn't turn Apps on — ${appsErrorMessage(error)}`);
    }
  }

  return (
    <EmptyState
      icon={PlugIcon}
      title="Apps are turned off"
      description="Your Composio key and every connection are kept — turn Apps back on to use them again."
      action={
        <Button type="button" disabled={!canWrite || enableApps.isPending} title={canWrite ? undefined : writeReason} onClick={() => void turnOn()}>
          {enableApps.isPending ? "Turning on…" : "Turn on"}
        </Button>
      }
    />
  );
}

function AppsHeader({
  status,
  onValidate,
  validating,
  onChanged,
}: {
  status: AppsStatusOut;
  onValidate: () => Promise<unknown>;
  validating: boolean;
  onChanged: () => void;
}) {
  const chip = composioStatusChip(status);
  const disableApps = useDisableApps();
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  async function confirmDisable() {
    try {
      await disableApps.mutateAsync();
      toast.success("Apps turned off");
      onChanged();
    } catch (error) {
      toast.error(`Couldn't turn Apps off — ${appsErrorMessage(error)}`);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip tone={chip.tone} dot>
            {chip.label}
          </StatusChip>
          {status.last_test_at ? (
            <span className="text-xs text-muted-foreground">
              Last tested <RelativeTime iso={status.last_test_at} />
            </span>
          ) : null}
          <span className="text-xs text-muted-foreground">
            {status.connections} connected · {status.paused_tools} paused
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canWrite || validating}
            title={canWrite ? undefined : writeReason}
            onClick={() => void onValidate()}
          >
            {validating ? "Validating…" : "Validate"}
          </Button>
          <EnableComposioDialog
            mode="rotate"
            credentialId={status.credential_id ?? undefined}
            trigger={
              <Button type="button" variant="outline" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
                Rotate
              </Button>
            }
            onDone={onChanged}
          />
          <ConfirmDialog
            trigger={
              <Button type="button" variant="ghost" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
                Disable
              </Button>
            }
            title="Turn off Apps?"
            description="The key and every connection are kept. Tools that use them switch off until you turn Apps back on."
            confirmLabel="Turn off"
            onConfirm={confirmDisable}
          />
        </div>
      </div>
      <Link href="/console/keys" className="w-fit text-[0.8125rem] font-medium text-foreground underline underline-offset-2">
        Also in Keys
      </Link>
    </div>
  );
}
