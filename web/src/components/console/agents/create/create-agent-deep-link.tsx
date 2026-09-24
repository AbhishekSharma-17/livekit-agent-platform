"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { useWriteAccess } from "@/components/console/lib/roles";

import { CreateAgentDialog } from "./create-agent-dialog";

/**
 * `/console/agents/new` as a deep link (R-V4-2, docs/v4/TEMPLATES.md §6.1):
 * the agents list renders underneath and this opens the New agent dialog over
 * it. `?template=<id>` preselects a starter. Closing without creating goes
 * back to `/console/agents` (`replace`, so Back doesn't reopen the dialog);
 * creating navigates to the new agent instead. A viewer never gets the dialog
 * — the page's "New agent" button already explains why it's disabled.
 */
export function CreateAgentDeepLink() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const templateId = searchParams.get("template");
  const { canWrite, isLoading } = useWriteAccess();
  const [open, setOpen] = React.useState(true);
  const created = React.useRef(false);

  if (isLoading || !canWrite) return null;

  return (
    <CreateAgentDialog
      open={open}
      initialTemplateId={templateId}
      onCreated={() => {
        created.current = true;
      }}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next && !created.current) router.replace("/console/agents");
      }}
    />
  );
}
