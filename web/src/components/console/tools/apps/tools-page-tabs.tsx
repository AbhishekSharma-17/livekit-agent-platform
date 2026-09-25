"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolsList } from "@/components/console/tools/tools-list";
import { AppsTab } from "@/components/console/tools/apps/apps-tab";

/**
 * `/console/tools?tab=tools|apps` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1;
 * docs/v5/COMPOSIO.md §6, V5-22). `ToolsList` (V4-13's file — not touched
 * here) keeps its own `PageHeader`, so the "Tools" tab's header lives inside
 * that tab rather than above the strip; "Apps" has none of its own.
 *
 * `?connect=ok|error` is the Composio callback's redirect
 * (`GET /v1/tool-providers/composio/callback`, docs/v5/COMPOSIO.md §4): a
 * toast, an Apps cache refresh, then the query params are replaced so a page
 * refresh doesn't repeat the toast.
 */
export function ToolsPageTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const requested = searchParams.get("tab");
  const active = requested === "apps" ? "apps" : "tools";
  const connect = searchParams.get("connect");

  React.useEffect(() => {
    if (connect !== "ok" && connect !== "error") return;
    if (connect === "ok") toast.success("App connected");
    else toast.error("Couldn't connect the app. Try again.");
    void queryClient.invalidateQueries({ queryKey: ["apps"] });
    router.replace("/console/tools?tab=apps", { scroll: false });
    // Runs once per `connect` value; `router`/`queryClient` are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connect]);

  return (
    <Tabs value={active} onValueChange={(next) => router.replace(`/console/tools?tab=${next}`, { scroll: false })}>
      <TabsList>
        <TabsTrigger value="tools">Tools</TabsTrigger>
        <TabsTrigger value="apps">Apps</TabsTrigger>
      </TabsList>
      <TabsContent value="tools" className="pt-4">
        <ToolsList />
      </TabsContent>
      <TabsContent value="apps" className="pt-4">
        <AppsTab />
      </TabsContent>
    </Tabs>
  );
}
