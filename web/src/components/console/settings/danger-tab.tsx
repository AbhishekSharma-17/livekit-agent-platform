"use client";

import { OctagonAlertIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Section, SectionRow } from "@/components/shared/section";
import { useActiveWorkspace } from "./use-settings-queries";

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
  );
}
