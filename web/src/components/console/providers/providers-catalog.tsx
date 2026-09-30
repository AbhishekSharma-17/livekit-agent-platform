"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BlocksIcon, ChevronRightIcon } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsCount, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/shared/empty-state";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { StatusPill } from "@/components/shared/status-chip";
import { ProviderRow } from "@/components/console/providers/provider-row";
import { RowsSkeleton } from "@/components/console/registry/rows-skeleton";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import { useProviderList } from "@/hooks/useProviders";
import { KIND_LABEL, type ProviderKind } from "@/components/console/registry/provider-meta";
import type { ProviderOut } from "@/contracts/lkap-contracts";

/**
 * `/console/providers?kind=` (UI_UX_SPEC-V2-AMENDMENTS §2.2). Groups VAD,
 * turn detection and noise cancellation into one tab ("VAD/Turn/NC") since
 * the spec lists them together; every other kind gets its own tab, in
 * `KIND_ORDER`. `incompatible`/`removed` entries collapse under "Not
 * available" with the reason, per the spec's "so nobody files a bug".
 */
const TAB_GROUPS: { id: string; label: string; kinds: ProviderKind[] }[] = [
  { id: "realtime", label: KIND_LABEL.realtime, kinds: ["realtime"] },
  { id: "stt", label: KIND_LABEL.stt, kinds: ["stt"] },
  { id: "llm", label: KIND_LABEL.llm, kinds: ["llm"] },
  { id: "tts", label: KIND_LABEL.tts, kinds: ["tts"] },
  { id: "avatar", label: KIND_LABEL.avatar, kinds: ["avatar"] },
  { id: "vad_turn_nc", label: "VAD/Turn/NC", kinds: ["vad", "turn_detection", "noise_cancellation"] },
  { id: "embedding", label: KIND_LABEL.embedding, kinds: ["embedding"] },
  { id: "image_gen", label: KIND_LABEL.image_gen, kinds: ["image_gen"] },
  { id: "secret_bag", label: KIND_LABEL.secret_bag, kinds: ["secret_bag"] },
  // docs/v5/_asks.md #4 (V5-18): this page had no group for `tool_provider`,
  // so Composio showed only in the Keys dialog. `ProviderRow` renders it
  // read-only (see there) since its "Enable" is a different flow — Tools ->
  // Apps' Enable/Disable, not the generic per-connection install toggle.
  { id: "tool_provider", label: KIND_LABEL.tool_provider, kinds: ["tool_provider"] },
  // V5-25 (D-V5-7): the vendors the `web_search` and `send_sms` built-ins call.
  { id: "web_search", label: KIND_LABEL.web_search, kinds: ["web_search"] },
  { id: "sms", label: KIND_LABEL.sms, kinds: ["sms"] },
  // V5-20: vector stores and re-ranking services a knowledge connection uses (Settings → Knowledge connections, V5-24).
  { id: "knowledge", label: KIND_LABEL.knowledge, kinds: ["knowledge"] },
];
const TAB_IDS = new Set(TAB_GROUPS.map((t) => t.id));
const DEFAULT_TAB = TAB_GROUPS[0]!.id;

//: `lkap_contracts.providers.MCP_OAUTH_PROVIDER_ID` (not exported to the generated `.d.ts` —
//: it's a plain module constant, not a pydantic model). Hidden here for the same reason as
//: `credential-list.tsx`'s Keys page (docs/v5/_asks.md #108, V5-14/V5-21): it is a
//: `secret_bag` registry entry with `requires_credential: false` and no secret fields,
//: created only by signing in to an MCP server from its tool page — "Enable" here would let
//: an admin create a useless hand-made row.
const MCP_OAUTH_PROVIDER_ID = "mcp-oauth";

function isAvailable(provider: ProviderOut): boolean {
  return (provider.availability ?? "available") === "available";
}

/**
 * Loading state for the Providers page: the kind tab strip and provider
 * rows with their vendor mark, so the data replaces it without a jump. Also
 * the page's `Suspense` fallback.
 */
export function ProvidersCatalogSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <div aria-hidden="true" className="flex h-10 items-center gap-5 overflow-hidden border-b border-border">
        {[64, 88, 72, 96, 60, 80].map((width, index) => (
          <Skeleton key={index} className="h-3.5 shrink-0" style={{ width }} />
        ))}
      </div>
      <RowsSkeleton label="Loading providers" mark />
    </div>
  );
}

/**
 * The provider registry by kind (docs/ui/DESIGN-SYSTEM.md section 7.4,
 * "List"): tabs per kind with counts, search once a kind has 6 or more
 * providers (accent-insensitive, highlighted, remembered), distinct "nothing
 * of this kind" and "no matches" states, and the unavailable entries folded
 * away with their reason.
 */
export function ProvidersCatalog() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedKind = searchParams?.get("kind");
  const activeTab = requestedKind && TAB_IDS.has(requestedKind) ? requestedKind : DEFAULT_TAB;

  const { data, isLoading, isError, error, refetch } = useProviderList();
  const connectionsQuery = useConnections();
  const connections = connectionsQuery.data?.items ?? [];

  const providers = React.useMemo(() => (data?.providers ?? []).filter((p) => p.id !== MCP_OAUTH_PROVIDER_ID), [data]);
  const group = TAB_GROUPS.find((t) => t.id === activeTab) ?? TAB_GROUPS[0]!;
  const inKind = React.useMemo(() => providers.filter((p) => group.kinds.includes(p.kind)), [providers, group]);
  const search = useListSearch("providers", inKind, (provider) => [provider.label, provider.vendor, provider.notes]);

  function selectTab(next: string) {
    router.replace(`/console/providers?kind=${next}`, { scroll: false });
  }

  if (isLoading) return <ProvidersCatalogSkeleton />;
  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load the providers" }} onRetry={() => void refetch()} />;
  }

  const available = search.filtered.filter(isAvailable);
  const notAvailable = search.filtered.filter((p) => !isAvailable(p));

  return (
    <Tabs value={activeTab} onValueChange={selectTab}>
      <TabsList aria-label="Provider kind">
        {TAB_GROUPS.map((tab) => {
          const count = providers.filter((p) => tab.kinds.includes(p.kind) && isAvailable(p)).length;
          return (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
              {count > 0 ? <TabsCount>{count}</TabsCount> : null}
            </TabsTrigger>
          );
        })}
      </TabsList>
      <TabsContent value={activeTab} className="flex flex-col gap-4">
        <ListSearchField search={search} label="Search providers" total={inKind.length} className="mb-0" />
        {inKind.length === 0 ? (
          <EmptyState
            icon={BlocksIcon}
            title={`No ${group.label.toLowerCase()} providers yet`}
            description="This kind has no providers in the registry on this server."
          />
        ) : search.noMatches ? (
          <ListNoMatches search={search} items="providers" />
        ) : (
          <>
            {available.length > 0 ? (
              <div className="flex flex-col gap-2">
                {available.map((provider) => (
                  <ProviderRow key={provider.id} provider={provider} connections={connections} query={search.query} />
                ))}
              </div>
            ) : null}
            {notAvailable.length > 0 ? <NotAvailableSection providers={notAvailable} query={search.query} /> : null}
          </>
        )}
      </TabsContent>
    </Tabs>
  );
}

function NotAvailableSection({ providers, query }: { providers: ProviderOut[]; query: string }) {
  return (
    <Collapsible>
      <CollapsibleTrigger className="group/more inline-flex items-center gap-1 rounded-sm text-control font-medium text-text-secondary hover:text-foreground">
        <ChevronRightIcon
          className="size-4 transition-transform duration-(--duration-base) group-data-[state=open]/more:rotate-90"
          aria-hidden="true"
        />
        Not available ({providers.length})
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
        {providers.map((provider) => (
          <div key={provider.id} className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-3">
            <div className="min-w-0">
              <p className="text-body text-foreground">
                <Highlight text={provider.label} query={query} />
              </p>
              <p className="text-caption text-pretty text-text-secondary">{provider.notes || reasonFor(provider)}</p>
            </div>
            <StatusPill tone="neutral" size="sm">
              {provider.availability === "removed" ? "Removed" : provider.availability === "incompatible" ? "Not available" : "Coming soon"}
            </StatusPill>
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

function reasonFor(provider: ProviderOut): string {
  switch (provider.availability) {
    case "removed":
      return "No longer offered.";
    case "incompatible":
      return "Known not to work with this platform.";
    default:
      return "Planned for a later release.";
  }
}
