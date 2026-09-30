"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { LoadingRegion } from "@/components/shared/loading-state";
import { Page, PageHeader } from "@/components/shared/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolsAddActions, ToolsList } from "@/components/console/tools/tools-list";
import { AppsTab } from "@/components/console/tools/apps/apps-tab";

const TITLE = "Tools";
const DESCRIPTION = "The APIs, MCP servers, lookup tables and apps your agents can call.";

/**
 * `/console/tools?tab=tools|apps` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1;
 * docs/v5/COMPOSIO.md §6, V5-22). One page header above the tab strip
 * (docs/ui/DESIGN-SYSTEM.md section 7.3): on the Tools tab it carries the add
 * actions with "Add HTTP tool" as the one primary; the Apps tab's main action
 * lives in its own state (Enable Composio, Turn on).
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
    <Page>
      <PageHeader title={TITLE} description={DESCRIPTION} actions={active === "tools" ? <ToolsAddActions /> : undefined} />
      <Tabs value={active} onValueChange={(next) => router.replace(`/console/tools?tab=${next}`, { scroll: false })}>
        <TabsList>
          <TabsTrigger value="tools">Tools</TabsTrigger>
          <TabsTrigger value="apps">Apps</TabsTrigger>
        </TabsList>
        <TabsContent value="tools" className="pt-5">
          <ToolsList />
        </TabsContent>
        <TabsContent value="apps" className="pt-5">
          <AppsTab />
        </TabsContent>
      </Tabs>
    </Page>
  );
}

/**
 * The route's `Suspense` fallback (`useSearchParams` suspends on the first
 * render): the real header text, a tab strip and list rows, so the page is
 * never blank while it resolves (docs/ui/DESIGN-SYSTEM.md section 8.1).
 */
export function ToolsPageSkeleton() {
  return (
    <Page>
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <LoadingRegion label="Loading tools">
        <div className="flex h-10 items-end gap-6 border-b border-border">
          <Skeleton className="mb-2.5 h-4 w-12" />
          <Skeleton className="mb-2.5 h-4 w-10" />
        </div>
        <div className="flex flex-col gap-2 pt-5">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-14 w-full" />
          ))}
        </div>
      </LoadingRegion>
    </Page>
  );
}
