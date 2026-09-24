"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConnectionOverview } from "@/components/console/connections/connection-overview";
import { DeployPanel } from "@/components/console/connections/deploy-panel";
import { FleetCard } from "@/components/console/connections/fleet-card";
import { StorageTab } from "@/components/console/connections/storage-tab";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

const TAB_IDS = ["overview", "fleet", "storage", "deploy"] as const;
type TabId = (typeof TAB_IDS)[number];
const DEFAULT_TAB: TabId = "overview";

/** `/console/connections/[id]?tab=overview|fleet|storage|deploy` (UI_UX_SPEC-V2-AMENDMENTS §1). */
export function ConnectionDetailTabs({ connection }: { connection: ConnectionOut }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requested = searchParams?.get("tab");
  const active: TabId = requested && (TAB_IDS as readonly string[]).includes(requested) ? (requested as TabId) : DEFAULT_TAB;

  return (
    <Tabs
      value={active}
      onValueChange={(next) => {
        router.replace(`/console/connections/${connection.id}?tab=${next}`, { scroll: false });
      }}
    >
      <TabsList className="h-auto flex-wrap justify-start group-data-horizontal/tabs:h-auto">
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="fleet">Fleet</TabsTrigger>
        <TabsTrigger value="storage">Storage</TabsTrigger>
        <TabsTrigger value="deploy">Deploy</TabsTrigger>
      </TabsList>
      <TabsContent value="overview" className="pt-4">
        <ConnectionOverview connection={connection} />
      </TabsContent>
      <TabsContent value="fleet" className="pt-4">
        <FleetCard connection={connection} />
      </TabsContent>
      <TabsContent value="storage" className="pt-4">
        <StorageTab connection={connection} />
      </TabsContent>
      <TabsContent value="deploy" className="pt-4">
        <DeployPanel connection={connection} />
      </TabsContent>
    </Tabs>
  );
}
