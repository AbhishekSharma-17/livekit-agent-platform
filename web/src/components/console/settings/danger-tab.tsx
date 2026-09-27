"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { OctagonAlertIcon } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Section, SectionRow } from "@/components/shared/section";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
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
 * notice, never a raw error — the card's own rule for an optional extra.
 */
function PurgeMemoryDialog({ workspaceName }: { workspaceName: string }) {
  const { canWrite: canPurge } = useWriteAccess("admin");
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const matches = typed.trim().length > 0 && typed.trim() === workspaceName;

  const purge = useMutation({
    mutationFn: () => api.post<MemoryPurgeOut>("memory/purge", { confirm: true }),
    onSuccess: (result) => {
      toast.success(
        result.status === "nothing_to_purge"
          ? "There were no caller memories to delete."
          : `Deleting ${result.subjects} caller${result.subjects === 1 ? "'s" : "s'"} memories.`,
      );
      setOpen(false);
      setTyped("");
    },
    onError: (err: unknown) => {
      toast.error(
        err instanceof ApiError && err.code === "memory_unavailable"
          ? MEMORY_UNAVAILABLE_NOTICE
          : err instanceof ApiError
            ? err.message
            : "Couldn't delete caller memories",
      );
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setTyped("");
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="destructive"
          disabled={!canPurge}
          title={canPurge ? undefined : writeAccessReason("admin")}
        >
          Delete all caller memories
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete all caller memories?</DialogTitle>
          <DialogDescription>
            Deletes what every agent in this workspace remembers about every caller, all at once. This
            can&apos;t be undone. Type the workspace name, <strong className="font-mono">{workspaceName}</strong>,
            to confirm.
          </DialogDescription>
        </DialogHeader>
        <Field label="Workspace name" htmlFor="purge-memory-confirm">
          <Input
            id="purge-memory-confirm"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder={workspaceName}
          />
        </Field>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
            disabled={!matches || purge.isPending}
            onClick={() => purge.mutate()}
          >
            {purge.isPending ? "Deleting…" : "Delete all caller memories"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
  const { workspace: membership } = useActiveWorkspace();
  const isOwner = membership?.role === "owner";

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="danger-memory"
        title="Caller memory"
        description="Every caller memory this workspace's agents have stored, across every agent that shares it."
      >
        <SectionRow>
          <PurgeMemoryDialog workspaceName={membership?.name ?? ""} />
        </SectionRow>
      </Section>

      <Section id="danger" title="Danger zone" description="Irreversible workspace actions.">
        <SectionRow>
          <Alert variant="warning">
            <OctagonAlertIcon />
            <AlertTitle>Deleting a workspace isn&apos;t available yet</AlertTitle>
            <AlertDescription>
              {isOwner
                ? "There is no delete-workspace or transfer-ownership endpoint in this release. To remove access instead, revoke members from the Team tab or disable API keys and webhooks."
                : "Only the workspace owner can see workspace-deletion controls, and none exist in this release yet."}
            </AlertDescription>
          </Alert>
        </SectionRow>
      </Section>
    </div>
  );
}
