"use client";

import * as React from "react";
import { DownloadIcon, EyeIcon, LayoutPanelLeftIcon, PaperclipIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { PanelBlock } from "@/panels/generic/blocks";
import type { AgentOut, AgentPublicOut, SessionAssetOut, SessionAssetPage, SessionDetailOut } from "@/contracts/lkap-contracts";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { normalizeUiState } from "@/lib/ui-state";
import { storedPanelLayout } from "@/panels/composite/layout";
import { resolvePanel, type PanelProps } from "@/panels/registry";

import { useSessionAgent } from "./use-session-queries";

/**
 * "Panel at end of call" (docs/UI_UX_SPEC.md §7.8 item 3): the stored
 * `final_ui_state` rendered read-only through the panel registry, plus
 * (V5-23) the session's stored files (`GET /v1/sessions/{id}/assets`,
 * `SessionAssetPage` — the console-only route: `sessions:read`, unlike the
 * worker's own internal asset routes). The panel id comes from the agent
 * (`SessionDetailOut` carries none); `resolvePanel` falls back to the
 * generic panel for unknown ids and for a deleted agent. `composite` renders
 * the agent's current blocks (V2-11).
 *
 * The panel snapshot has no room (the call has ended, so no `lkap.ui.asset`
 * bytes will ever arrive) — `GalleryBlock`/`UploadBlock`/`DocumentBlock` all
 * read a stored asset's picture through `panel.assets`, so this tab builds
 * that map from the same `SessionAssetPage`'s signed `url`s instead of the
 * usual blob-URL map (`docs/v5/_asks.md` #131: "renders stored assets
 * through the signed URL when stored").
 */

const NO_TRANSCRIPT: never[] = [];
const EMPTY_ASSET_URLS = new Map<string, string>();
const noopPerform: PanelProps["perform"] = async () => undefined;

/** The signed `url` on a `SessionAssetPage` row is good for 15 minutes; refetch well before it lapses. */
const SESSION_ASSETS_REFETCH_MS = 10 * 60 * 1000;

/** `GET /v1/sessions/{id}/assets` — every file the session's caller sent or the agent pinned/copied in, newest last. */
export function useSessionAssets(sessionId: string) {
  return useQuery({
    queryKey: ["sessions", sessionId, "assets"] as const,
    queryFn: () => api.get<SessionAssetPage>(`sessions/${sessionId}/assets`),
    enabled: sessionId.length > 0,
    refetchInterval: SESSION_ASSETS_REFETCH_MS,
  });
}

/** `asset_id → signed url`, for a block that would otherwise read a live blob URL off `panel.assets`. */
export function sessionAssetUrls(items: SessionAssetOut[] | undefined): Map<string, string> {
  return new Map((items ?? []).filter((item): item is SessionAssetOut & { url: string } => Boolean(item.url)).map((item) => [item.id, item.url]));
}

/** Name, type, size, download — the session detail's own list, independent of whether any block shows the file. */
export function SessionAssetsCard({ items, isLoading }: { items: SessionAssetOut[] | undefined; isLoading: boolean }) {
  if (isLoading) return <Skeleton className="h-24 w-full" />;
  if (!items || items.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <PanelBlock title="Files" count={items.length}>
        <ul data-slot="session-assets-list" className="flex flex-col gap-2.5">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-2.5">
              <Icon as={PaperclipIcon} size="sm" className="text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{item.name}</p>
                <p className="text-muted-foreground text-xs">
                  {item.mime} · {formatBytes(item.size)}
                </p>
              </div>
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex shrink-0 items-center gap-1 rounded-xs text-xs focus-visible:ring-2 focus-visible:outline-none"
                >
                  <Icon as={DownloadIcon} size="sm" />
                  Download
                </a>
              ) : (
                <span className="text-muted-foreground shrink-0 text-xs">Link expired</span>
              )}
            </li>
          ))}
        </ul>
      </PanelBlock>
    </div>
  );
}

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
  const assetsQuery = useSessionAssets(session.id);

  return (
    <div data-slot="session-panel-tab" className="flex flex-col gap-4">
      <SessionAssetsCard items={assetsQuery.data?.items} isLoading={assetsQuery.isLoading} />
      {!session.final_ui_state ? (
        <EmptyState
          icon={LayoutPanelLeftIcon}
          title="No panel state was saved for this call"
          description="The agent saves the panel when the call ends; this session ended before it could."
        />
      ) : agentQuery.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <PanelSnapshot session={session} agent={agentQuery.data} assetUrls={sessionAssetUrls(assetsQuery.data?.items)} />
      )}
    </div>
  );
}

export function PanelSnapshot({
  session,
  agent,
  assetUrls,
}: {
  session: SessionDetailOut;
  agent: AgentOut | undefined;
  /** `asset_id → signed url` (`sessionAssetUrls`); defaults to none for a caller that hasn't fetched it. */
  assetUrls?: Map<string, string>;
}) {
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
          assets={assetUrls ?? EMPTY_ASSET_URLS}
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
