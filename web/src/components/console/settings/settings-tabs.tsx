"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  BotIcon,
  Building2Icon,
  CpuIcon,
  HardDriveIcon,
  KeyRoundIcon,
  ShieldCheckIcon,
  SunIcon,
  TriangleAlertIcon,
  UsersIcon,
  WebhookIcon,
  type LucideIcon,
} from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMediaQuery } from "@/hooks/use-media-query";
import { AppearanceTab } from "./appearance-tab";
import { EnvironmentTab } from "./environment-tab";
import { WorkspaceTab } from "./workspace-tab";
import { TeamTab } from "./team-tab";
import { ApiKeysTab } from "./api-keys-tab";
import { AiAgentsTab } from "./ai-agents-tab";
import { WebhooksTab } from "./webhooks-tab";
import { StorageTab } from "./storage-tab";
import { ComplianceTab } from "./compliance-tab";
import { DangerTab } from "./danger-tab";

/**
 * `/console/settings?tab=` (decision D5, docs/ui/AUDIT.md): a vertical
 * section nav on the left, one section on the right. The nav is a vertical
 * tab list, so `?tab=` stays the single source of truth and only the active
 * section mounts (Radix unmounts inactive `TabsContent`), which keeps each
 * section's queries from firing until someone opens it. Below 768 px the same
 * list becomes a horizontal tab strip that scrolls sideways above the content.
 *
 * Tab ids are stable deep links used elsewhere (`?tab=workspace#account`,
 * `?tab=webhooks`); never rename one. A section that moves to another page
 * keeps its old link through `MOVED_SETTINGS_TABS` (`moved-tabs.ts`), which
 * the settings page redirects.
 */
interface TabDef {
  id: string;
  label: string;
  icon: LucideIcon;
  content: React.ReactNode;
}

const TABS: TabDef[] = [
  { id: "workspace", label: "Workspace", icon: Building2Icon, content: <WorkspaceTab /> },
  { id: "appearance", label: "Appearance", icon: SunIcon, content: <AppearanceTab /> },
  { id: "team", label: "Team", icon: UsersIcon, content: <TeamTab /> },
  { id: "api-keys", label: "API keys", icon: KeyRoundIcon, content: <ApiKeysTab /> },
  { id: "ai-agents", label: "AI agents", icon: BotIcon, content: <AiAgentsTab /> },
  { id: "webhooks", label: "Webhooks", icon: WebhookIcon, content: <WebhooksTab /> },
  { id: "compliance", label: "Compliance", icon: ShieldCheckIcon, content: <ComplianceTab /> },
  { id: "storage", label: "Storage", icon: HardDriveIcon, content: <StorageTab /> },
  { id: "environment", label: "Environment", icon: CpuIcon, content: <EnvironmentTab /> },
  { id: "danger", label: "Danger zone", icon: TriangleAlertIcon, content: <DangerTab /> },
];

const DEFAULT_TAB = "workspace";
const TAB_IDS = new Set(TABS.map((t) => t.id));

export function SettingsTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requested = searchParams.get("tab");
  const active = requested && TAB_IDS.has(requested) ? requested : DEFAULT_TAB;
  // Desktop-first: the server renders the vertical nav; phones switch to the
  // primitive's sideways-scrolling tab strip after mount (arrow keys follow).
  const phone = useMediaQuery("(max-width: 767px)");

  return (
    <Tabs
      orientation={phone ? "horizontal" : "vertical"}
      value={active}
      onValueChange={(next) => {
        router.replace(`/console/settings?tab=${next}`, { scroll: false });
      }}
      className="max-md:flex-col md:grid md:grid-cols-[208px_minmax(0,1fr)] md:items-start md:gap-8"
    >
      <TabsList aria-label="Settings sections" className="md:sticky md:top-[calc(var(--layout-topbar)+16px)]">
        {TABS.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id} className="gap-2">
            <tab.icon aria-hidden="true" />
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {TABS.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="min-w-0">
          {tab.content}
        </TabsContent>
      ))}
    </Tabs>
  );
}
