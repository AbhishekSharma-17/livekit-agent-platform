"use client";

import * as React from "react";
import { EyeIcon, LayoutPanelLeftIcon } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import type { AgentOut, AgentPublicOut, SessionDetailOut } from "@/contracts/lkap-contracts";
import { normalizeUiState } from "@/lib/ui-state";
import { storedPanelLayout } from "@/panels/composite/layout";
import { resolvePanel, type PanelProps } from "@/panels/registry";

import { useSessionAgent } from "./use-session-queries";

/**
 * "Panel at end of call" (docs/UI_UX_SPEC.md §7.8 item 3): the stored
 * `final_ui_state` rendered read-only through the panel registry. The panel
 * id comes from the agent (`SessionDetailOut` carries none); `resolvePanel`
 * falls back to the generic panel for unknown ids and for a deleted agent.
 * `composite` renders the agent's current blocks (V2-11).
 */

const NO_ASSETS = new Map<string, string>();
const NO_TRANSCRIPT: never[] = [];
const noopPerform: PanelProps["perform"] = async () => undefined;

/** `AgentPublicOut` for the panel from the full agent, or from the session alone when the agent is gone. */
export function panelAgent(session: SessionDetailOut, agent: AgentOut | undefined): AgentPublicOut {
  if (agent) {
    return {
      id: agent.id,
      name: agent.name,
      slug: agent.slug,
      description: agent.description,
      capabilities: agent.config.capabilities ?? {},
      pipeline_mode: agent.config.pipeline.mode ?? session.pipeline_mode,
      ui_panel_id: agent.ui_panel_id,
      panel: storedPanelLayout(agent.config.panel, agent.ui_panel_id),
    };
  }
  return {
    id: session.agent_id,
    name: session.agent_name,
    slug: "",
    description: "",
    capabilities: {},
    pipeline_mode: session.pipeline_mode,
    ui_panel_id: "",
    panel: { panel_id: "", blocks: [] },
  };
}

export function SessionPanelTab({ session }: { session: SessionDetailOut }) {
  const agentQuery = useSessionAgent(session.final_ui_state ? session.agent_id : "");

  if (!session.final_ui_state) {
    return (
      <EmptyState
        icon={LayoutPanelLeftIcon}
        title="No panel state was saved for this call"
        description="The agent saves the panel when the call ends; this session ended before it could."
      />
    );
  }

  if (agentQuery.isLoading) {
    return <Skeleton className="h-96 w-full" />;
  }

  return <PanelSnapshot session={session} agent={agentQuery.data} />;
}

export function PanelSnapshot({ session, agent }: { session: SessionDetailOut; agent: AgentOut | undefined }) {
  const publicAgent = React.useMemo(() => panelAgent(session, agent), [session, agent]);
  const panel = resolvePanel(publicAgent.ui_panel_id);
  const state = React.useMemo(
    () => (session.final_ui_state ? normalizeUiState(session.final_ui_state) : null),
    [session.final_ui_state],
  );
  if (!state) return null;
  const Panel = panel.Component;
  const fellBack = Boolean(publicAgent.ui_panel_id) && publicAgent.ui_panel_id !== panel.id;

  return (
    <div data-slot="session-panel-snapshot" data-panel-id={panel.id} className="space-y-3">
      <Alert variant="info">
        <Icon as={EyeIcon} size="md" />
        <AlertDescription>
          Read-only snapshot of the panel when the call ended.
          {fellBack
            ? ` This agent's “${publicAgent.ui_panel_id}” panel isn't available yet, so the generic panel shows the same data.`
            : null}
          {!agent ? " The agent no longer exists, so the generic panel shows the saved data." : null}
        </AlertDescription>
      </Alert>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <Panel
          state={state}
          assets={NO_ASSETS}
          agent={publicAgent}
          sessionId={session.id}
          perform={noopPerform}
          transcript={NO_TRANSCRIPT}
          connectionState="disconnected"
        />
      </div>
    </div>
  );
}
