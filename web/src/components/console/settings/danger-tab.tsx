"use client";

import * as React from "react";
import { toast } from "sonner";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { Section, SectionRow } from "@/components/shared/section";
import { TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { IfCan, readOnlyCopy } from "@/components/console/shared/permission";
import { ApiError, api } from "@/lib/api";
import type { MemoryPurgeOut } from "@/contracts/lkap-contracts";
import { useActiveWorkspace } from "./use-settings-queries";

const MEMORY_UNAVAILABLE_NOTICE = "Memory is not installed on this server.";

/**
 * "Delete all caller memories" (V5-42, ask #263): `POST /v1/memory/purge
 * {confirm: true}` — deletes the workspace's memory key and every caller
 * record at once (so nothing stored can ever be tied to a caller again),
 * then the stored memories in the background. Needs `admin` (the api's
 * `/v1/memory` route policy, ask #258); a typed confirmation (the workspace
 * name) on top of that, since this is workspace-wide and can't be undone.
 * A 503 `memory_unavailable` (the memory add-on isn't installed) is a plain
 * notice in the dialog, never a raw error.
 */
function PurgeMemoryDialog({ workspaceName }: { workspaceName: string }) {
  return (
    <TypedConfirmDialog
      trigger={
        <Button type="button" variant="danger-outline">
          Delete all caller memories
        </Button>
      }
      title="Delete all caller memories?"
      description={
        <>
          Deletes what every agent in this workspace remembers about every caller, all at once. This can&apos;t be
          undone. Type the workspace name, <strong className="font-mono">{workspaceName}</strong>, to confirm.
        </>
      }
      confirmText={workspaceName}
      inputLabel="Workspace name"
      confirmLabel="Delete all caller memories"
      busyLabel="Deleting…"
      onConfirm={async () => {
        let result: MemoryPurgeOut;
        try {
          result = await api.post<MemoryPurgeOut>("memory/purge", { confirm: true });
        } catch (err) {
          // The dialog stays open and shows this in plain words.
          if (err instanceof ApiError && err.code === "memory_unavailable") throw new Error(MEMORY_UNAVAILABLE_NOTICE);
          throw err;
        }
        toast.success(
          result.status === "nothing_to_purge"
            ? "There were no caller memories to delete."
            : `Deleting ${result.subjects} caller${result.subjects === 1 ? "'s" : "s'"} memories.`,
        );
      }}
    >
      <ul className="list-disc pl-5">
        <li>Every caller&apos;s stored memories, across every agent that shares them</li>
        <li>The key that links a returning caller to their memories</li>
      </ul>
    </TypedConfirmDialog>
  );
}

/**
 * "Delete workspace, transfer ownership" needs `owner` (CONTRACTS-V2 §3.2),
 * but Phase 1 shipped no route for either — `routers/workspaces.py` has no
 * `DELETE /v1/workspaces/{id}` and no ownership-transfer endpoint (only
 * per-member role changes, which the Team tab already covers). Naming that
 * plainly here rather than a generic "coming soon" placeholder, so an owner
 * doesn't go looking for a button that was never built.
 */
export function DangerTab() {
  const { workspace: membership, isLoading } = useActiveWorkspace();
  const isOwner = membership?.role === "owner";

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="danger-memory"
        title="Caller memory"
        description="Every caller memory this workspace's agents have stored, across every agent that shares it."
      >
        <SectionRow>
          {/* The typed confirmation needs the real name: never offer it before the name is known. */}
          {isLoading || !membership?.name ? (
            <LoadingRegion label="Loading the workspace">
              <Skeleton className="h-[34px] w-56" />
            </LoadingRegion>
          ) : (
            <IfCan
              min="admin"
              fallback={<ReadOnlyNote variant="block">{readOnlyCopy("admin", "delete caller memories")}</ReadOnlyNote>}
            >
              <PurgeMemoryDialog workspaceName={membership.name} />
            </IfCan>
          )}
        </SectionRow>
      </Section>

      <Section id="danger" title="Danger zone" description="Irreversible workspace actions.">
        <SectionRow>
          <Alert tone="warning" title="Deleting a workspace isn't available yet">
            {isOwner
              ? "There is no way to delete a workspace or transfer ownership in this release. To remove access instead, remove members on the Team tab, or revoke API keys and webhooks."
              : "Only the workspace owner could delete this workspace, and that isn't available in this release yet."}
          </Alert>
        </SectionRow>
      </Section>
    </div>
  );
}
