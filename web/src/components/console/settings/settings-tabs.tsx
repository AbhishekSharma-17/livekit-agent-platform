"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AppearanceTab } from "./appearance-tab";
import { EnvironmentTab } from "./environment-tab";
import { WorkspaceTab } from "./workspace-tab";
import { TeamTab } from "./team-tab";
import { ApiKeysTab } from "./api-keys-tab";
import { AiAgentsTab } from "./ai-agents-tab";
import { WebhooksTab } from "./webhooks-tab";
import { StorageTab } from "./storage-tab";
import { DangerTab } from "./danger-tab";

/**
 * `/console/settings?tab=` shell (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.5,
 * §3: "Settings tabs replace the single page", tab ids `workspace | team |
 * api-keys | webhooks | storage | danger`). V2-14 owns the six v2 tabs;
 * `appearance` (the v1 theme control, kept here since it has nowhere else to
 * live now that the sidebar footer's `ThemeMenu` is the quick-access copy,
 * not the only one) and `environment` (v1's read-only `/v1/health` panel)
 * were already real. `ComingSoonTab` (WP-1's placeholder) is gone from this
 * registry — see `storage-tab.tsx`/`danger-tab.tsx` for the two tabs whose
 * backend genuinely doesn't exist yet; they say so plainly instead of
 * reusing that generic copy. `ai-agents` (v3, docs/v3/AGENT-ACCESS.md §5) is
 * V3-04's "Connect an AI agent" tab, between `api-keys` and `webhooks`.
 */
interface TabDef {
  id: string;
  label: string;
  content: React.ReactNode;
}

const TABS: TabDef[] = [
  { id: "workspace", label: "Workspace", content: <WorkspaceTab /> },
  { id: "appearance", label: "Appearance", content: <AppearanceTab /> },
  { id: "team", label: "Team", content: <TeamTab /> },
  { id: "api-keys", label: "API keys", content: <ApiKeysTab /> },
  { id: "ai-agents", label: "AI agents", content: <AiAgentsTab /> },
  { id: "webhooks", label: "Webhooks", content: <WebhooksTab /> },
  { id: "storage", label: "Storage", content: <StorageTab /> },
  { id: "environment", label: "Environment", content: <EnvironmentTab /> },
  { id: "danger", label: "Danger zone", content: <DangerTab /> },
];

const DEFAULT_TAB = "workspace";
const TAB_IDS = new Set(TABS.map((t) => t.id));

export function SettingsTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requested = searchParams.get("tab");
  const active = requested && TAB_IDS.has(requested) ? requested : DEFAULT_TAB;

  return (
    <Tabs
      value={active}
      onValueChange={(next) => {
        router.replace(`/console/settings?tab=${next}`, { scroll: false });
      }}
    >
      <TabsList className="h-auto flex-wrap justify-start group-data-horizontal/tabs:h-auto">
        {TABS.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {TABS.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="pt-4">
          {tab.content}
        </TabsContent>
      ))}
    </Tabs>
  );
}
