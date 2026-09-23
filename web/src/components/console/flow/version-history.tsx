"use client";

import * as React from "react";
import { HistoryIcon, RotateCcwIcon } from "lucide-react";
import { useFormContext } from "react-hook-form";
import { toast } from "sonner";

import { errorMessage } from "@/components/console/shared/error-banner";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AgentOut, ConfigVersionOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";

import { useAgentVersion, useAgentVersions, useRestoreVersion } from "./api";
import { diffRows, objectHash, preview, type DiffRow } from "./version-diff";
import { SkeletonRows } from "@/components/shared/loading-state";

/**
 * Version history (V2-16): every saved `config_version` of the agent, a diff
 * of the chosen version against the current one (`jsondiffpatch`, imported
 * lazily so it never reaches the editor's main chunk), and Restore — which
 * saves that config as a **new** version (the api never rewrites history).
 * Fills the editor's `versionHistory` slot (the rail's "History" link) and
 * sits in the flow canvas toolbar. A large modal (side drawers are not
 * allowed — UI_UX_SPEC-V2-AMENDMENTS §5): the version list and the diff sit
 * side by side from `md`, each scrolling on its own; stacked on phones.
 */
export function VersionHistory({ agent, variant = "link" }: { agent: AgentOut; variant?: "link" | "button" }) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      {variant === "link" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-xs font-medium underline underline-offset-2 hover:text-foreground"
        >
          History
        </button>
      ) : (
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Icon as={HistoryIcon} />
          History
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="xl" className="sm:h-[min(85dvh,52rem)]">
          {open ? <VersionHistoryBody agent={agent} onDone={() => setOpen(false)} /> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function VersionHistoryBody({ agent, onDone }: { agent: AgentOut; onDone: () => void }) {
  const versions = useAgentVersions(agent.id);
  const [selected, setSelected] = React.useState<number | null>(null);
  const [confirming, setConfirming] = React.useState(false);
  const version = useAgentVersion(agent.id, selected);
  const restore = useRestoreVersion(agent.id);
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();
  const form = useFormContext() as ReturnType<typeof useFormContext> | null;
  const dirty = Boolean(form?.formState.isDirty);
  const items = versions.data?.items ?? [];

  async function onRestore() {
    if (selected === null) return;
    try {
      const updated = await restore.mutateAsync(selected);
      toast.success(
        updated.config_version === agent.config_version
          ? `Version ${selected} matches the current configuration`
          : `Restored version ${selected} as version ${updated.config_version}`,
      );
      setConfirming(false);
      onDone();
    } catch (error) {
      toast.error(`Couldn't restore — ${errorMessage(error)}`);
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Version history</DialogTitle>
        <DialogDescription>
          Every save is a version. Restoring saves the old configuration as a new version.
        </DialogDescription>
      </DialogHeader>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain md:flex-row md:overflow-hidden">
        <div className="shrink-0 p-4 md:w-72 md:overflow-y-auto md:overscroll-contain md:border-r md:border-border">
          {versions.isLoading ? (
            <SkeletonRows label="Loading versions" rows={4} rowClassName="h-12" />
          ) : versions.isError ? (
            <p className="text-sm text-danger-text">Couldn&apos;t load versions — {errorMessage(versions.error)}</p>
          ) : (
            <ol aria-label="Versions" className="flex flex-col divide-y divide-border rounded-md border border-border">
              {items.map((item) => {
                const current = item.config_version === agent.config_version;
                const active = item.config_version === selected;
                return (
                  <li key={item.config_version}>
                    <button
                      type="button"
                      aria-pressed={active}
                      disabled={current}
                      onClick={() => setSelected(item.config_version)}
                      className={cn(
                        "flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset disabled:cursor-default",
                        active ? "bg-muted" : "hover:bg-muted/60",
                      )}
                    >
                      <span className="flex min-w-0 flex-col">
                        <span className="font-medium">Version {item.config_version}</span>
                        <span className="truncate text-xs text-muted-foreground">
                          <RelativeTime iso={item.created_at} />
                          {item.note ? ` · ${item.note}` : ""}
                        </span>
                      </span>
                      {current ? (
                        <StatusChip tone="success" size="sm">
                          Current
                        </StatusChip>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <div className="min-w-0 flex-1 border-t border-border p-4 md:overflow-y-auto md:overscroll-contain md:border-t-0">
          {selected !== null ? (
            <section aria-labelledby="version-diff-title" className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id="version-diff-title" className="text-sm font-medium">
                  Version {selected} → current (version {agent.config_version})
                </h3>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => setConfirming(true)}
                  disabled={!canWrite || !version.data}
                  title={canWrite ? undefined : writeReason}
                >
                  <Icon as={RotateCcwIcon} />
                  Restore
                </Button>
              </div>
              {version.data ? (
                <VersionDiff before={version.data} after={agent} />
              ) : version.isError ? (
                <p className="text-sm text-danger-text">Couldn&apos;t load version {selected}.</p>
              ) : (
                <SkeletonRows label="Loading this version" rows={4} rowClassName="h-6" />
              )}
            </section>
          ) : (
            <p className="text-sm text-muted-foreground">
              {items.length > 1
                ? "Pick a version to see what changed since then."
                : versions.isLoading
                  ? ""
                  : "Only the current version exists so far. Every save adds one."}
            </p>
          )}
        </div>
      </div>

      <Dialog open={confirming} onOpenChange={(next) => !restore.isPending && setConfirming(next)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Restore version {selected}?</DialogTitle>
            <DialogDescription>
              Its configuration is validated and saved as version {agent.config_version + 1}. Nothing is deleted —
              you can restore the current version the same way.
              {dirty ? " Unsaved changes in the editor are discarded." : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)} disabled={restore.isPending}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void onRestore()} disabled={restore.isPending}>
              {restore.isPending ? "Restoring…" : "Restore"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Rows of what changed between a stored version and the agent's current config. */
export function VersionDiff({ before, after }: { before: ConfigVersionOut; after: AgentOut }) {
  const [rows, setRows] = React.useState<DiffRow[] | null>(null);
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => {
    let cancelled = false;
    setRows(null);
    import("jsondiffpatch")
      .then(({ create }) => {
        const differ = create({ objectHash, arrays: { detectMove: true } });
        const delta = differ.diff(before.config ?? {}, after.config ?? {});
        if (!cancelled) setRows(diffRows(delta));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [before, after]);

  if (failed) return <p className="text-sm text-danger-text">Couldn&apos;t compute the difference.</p>;
  if (rows === null) return <p className="text-sm text-muted-foreground">Comparing…</p>;
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No differences.</p>;
  return (
    <ul aria-label="Changes" className="flex flex-col gap-2">
      {rows.map((row, index) => (
        <li key={`${row.path}-${index}`} className="rounded-md border border-border p-2 text-xs" data-diff-kind={row.kind}>
          <div className="flex items-center gap-2">
            <StatusChip
              size="sm"
              tone={row.kind === "added" ? "success" : row.kind === "removed" ? "danger" : "info"}
            >
              {row.kind}
            </StatusChip>
            <code className="truncate font-mono">{row.path || "(config)"}</code>
          </div>
          {row.kind === "changed" || row.kind === "removed" ? (
            <p className="mt-1 font-mono break-all text-danger-text">− {preview(row.before)}</p>
          ) : null}
          {row.kind === "changed" || row.kind === "added" ? (
            <p className="mt-1 font-mono break-all text-success-text">+ {preview(row.after)}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
