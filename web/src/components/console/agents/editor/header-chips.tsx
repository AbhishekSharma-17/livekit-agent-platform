"use client";

import { MessageSquareTextIcon, PlugIcon, WorkflowIcon } from "lucide-react";
import { useWatch } from "react-hook-form";

import { Icon } from "@/components/shared/icon";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import { connectionTypeLabel, useAgentConnection } from "./use-connection";

const CHIP =
  "inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-xs border border-border bg-card px-2 text-xs font-medium text-muted-foreground";

/**
 * Read-only connection chip (UI_UX_SPEC-V2-AMENDMENTS §2.3): name + type.
 * V2-13 replaces it through the `connectionChip` slot with the version that
 * opens the change popover.
 */
export function ConnectionChip({ agent }: { agent: AgentOut }) {
  const connectionId = agent.connection_id ?? null;
  const query = useAgentConnection(connectionId);

  let text: string;
  let title: string | undefined;
  if (!connectionId) {
    text = "Default connection";
    title = "Runs on the workspace's default LiveKit connection.";
  } else if (query.isLoading) {
    text = "Connection…";
  } else if (query.data) {
    text = `${query.data.name} · ${connectionTypeLabel(query.data)}`;
    title = query.data.url;
  } else {
    text = `Connection ${connectionId.slice(0, 8)}`;
    title = connectionId;
  }

  return (
    <span className={cn(CHIP)} title={title} data-slot="connection-chip">
      <Icon as={PlugIcon} size="sm" />
      <span className="sr-only">Connection: </span>
      <span className="truncate">{text}</span>
    </span>
  );
}

/**
 * Read-only mode chip (Prompt / Flow). V2-16 replaces it through the
 * `modeChip` slot with the version that opens the switch dialog.
 */
export function ModeChip() {
  const mode = useWatch<AgentEditorForm, "mode">({ name: "mode" }) ?? "prompt";
  const isFlow = mode === "flow";
  return (
    <span className={CHIP} data-slot="mode-chip">
      <Icon as={isFlow ? WorkflowIcon : MessageSquareTextIcon} size="sm" />
      <span className="sr-only">Mode: </span>
      {isFlow ? "Flow" : "Prompt"}
    </span>
  );
}
