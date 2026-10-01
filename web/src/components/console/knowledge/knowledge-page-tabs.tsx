"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { LoadingRegion } from "@/components/shared/loading-state";
import { Page, PageHeader } from "@/components/shared/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CreateKbDialog } from "@/components/console/knowledge/create-kb-dialog";
import { KbList } from "@/components/console/knowledge/kb-list";
import { AddKnowledgeConnectionButton, KnowledgeConnectionsTab } from "@/components/console/knowledge/knowledge-connections-tab";

const TITLE = "Knowledge";
const DESCRIPTION = "Documents your agents search for answers during a call.";

export type KnowledgeTab = "bases" | "connections";

/** The tab a `?tab=` value opens: the knowledge base list unless it asks for connections. */
export function knowledgeTabFor(requested: string | null): KnowledgeTab {
  return requested === "connections" ? "connections" : "bases";
}

/** The URL for a tab; the list keeps the bare path. */
export function knowledgeTabHref(tab: KnowledgeTab): string {
  return tab === "connections" ? "/console/knowledge?tab=connections" : "/console/knowledge";
}

/**
 * `/console/knowledge?tab=connections` (UI-R1): knowledge bases (the default)
 * and the knowledge connections they can be stored in, the second moved here
 * from Settings. One page header above the tab strip (docs/ui/DESIGN-SYSTEM.md
 * section 7.3) carries each tab's one primary: "New knowledge base" on the
 * list, "Add connection" on Connections.
 */
export function KnowledgePageTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const active = knowledgeTabFor(searchParams.get("tab"));

  return (
    <Page>
      <PageHeader
        title={TITLE}
        description={DESCRIPTION}
        actions={active === "bases" ? <CreateKbDialog /> : <AddKnowledgeConnectionButton />}
      />
      <Tabs
        value={active}
        onValueChange={(next) => router.replace(knowledgeTabHref(knowledgeTabFor(next)), { scroll: false })}
      >
        <TabsList>
          <TabsTrigger value="bases">Knowledge bases</TabsTrigger>
          <TabsTrigger value="connections">Connections</TabsTrigger>
        </TabsList>
        <TabsContent value="bases" className="pt-5">
          <KbList />
        </TabsContent>
        <TabsContent value="connections" className="pt-5">
          <KnowledgeConnectionsTab addAction="page" />
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
export function KnowledgePageSkeleton() {
  return (
    <Page>
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <LoadingRegion label="Loading knowledge">
        <div className="flex h-10 items-end gap-6 border-b border-border">
          <Skeleton className="mb-2.5 h-4 w-28" />
          <Skeleton className="mb-2.5 h-4 w-20" />
        </div>
        <div className="flex flex-col gap-2 pt-5">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-14 w-full" />
          ))}
        </div>
      </LoadingRegion>
    </Page>
  );
}
