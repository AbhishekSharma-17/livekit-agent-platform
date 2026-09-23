import { WorkflowIcon } from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";

/**
 * Placeholder for the `flow` section (visible only when `mode === "flow"`).
 * V2-16 replaces it through an `EditorExtension` with the flow canvas
 * (`layout: "full"`); see `../README.md`.
 */
export function FlowSection() {
  return (
    <div className="rounded-lg border border-border bg-card">
      <EmptyState
        icon={WorkflowIcon}
        title="This agent runs a flow"
        description="Calls follow the saved flow. Editing it needs the flow builder, which isn't installed in this console."
      />
    </div>
  );
}
