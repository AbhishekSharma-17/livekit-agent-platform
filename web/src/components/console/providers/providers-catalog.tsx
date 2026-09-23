"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronRightIcon } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { StatusChip } from "@/components/shared/status-chip";
import { ProviderRow } from "@/components/console/providers/provider-row";
import { errorMessage, ErrorBanner } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import { useProviderList } from "@/hooks/useProviders";
import { KIND_LABEL, type ProviderKind } from "@/components/console/registry/provider-meta";
import type { ProviderOut } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";

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
];
const TAB_IDS = new Set(TAB_GROUPS.map((t) => t.id));
const DEFAULT_TAB = TAB_GROUPS[0]!.id;

export function ProvidersCatalog() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedKind = searchParams?.get("kind");
  const activeTab = requestedKind && TAB_IDS.has(requestedKind) ? requestedKind : DEFAULT_TAB;
  const [search, setSearch] = React.useState("");

  const { data, isLoading, isError, error, refetch } = useProviderList();
  const connectionsQuery = useConnections();
  const connections = connectionsQuery.data?.items ?? [];

  function selectTab(next: string) {
    router.replace(`/console/providers?kind=${next}`, { scroll: false });
  }

  if (isLoading) {
    return (
      <LoadingRegion label="Loading providers" className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </LoadingRegion>
    );
  }
  if (isError) {
    return <ErrorBanner message={`Couldn't load the provider registry — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const providers = data?.providers ?? [];
  const group = TAB_GROUPS.find((t) => t.id === activeTab) ?? TAB_GROUPS[0]!;
  const needle = search.trim().toLowerCase();

  const inKind = providers.filter((p) => group.kinds.includes(p.kind));
  const matching = needle === "" ? inKind : inKind.filter((p) => p.label.toLowerCase().includes(needle) || p.vendor.toLowerCase().includes(needle));

  const available = matching.filter((p) => (p.availability ?? "available") === "available");
  const notAvailable = matching.filter((p) => (p.availability ?? "available") !== "available");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search providers"
          className="w-full sm:w-64"
          aria-label="Search providers"
        />
      </div>

      <div role="tablist" aria-label="Provider kind" className="flex flex-wrap gap-1 border-b border-border pb-2">
        {TAB_GROUPS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => selectTab(tab.id)}
            className={`rounded-sm px-3 py-1.5 text-sm font-medium outline-none transition-colors duration-(--dur-2) focus-visible:ring-2 focus-visible:ring-ring ${
              activeTab === tab.id ? "bg-secondary text-secondary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {available.length === 0 && notAvailable.length === 0 ? (
        <EmptyState title="No providers match" description="Try a different search term or kind." compact />
      ) : (
        <div className="flex flex-col gap-2">
          {available.map((provider) => (
            <ProviderRow key={provider.id} provider={provider} connections={connections} />
          ))}
        </div>
      )}

      {notAvailable.length > 0 ? <NotAvailableSection providers={notAvailable} /> : null}
    </div>
  );
}

function NotAvailableSection({ providers }: { providers: ProviderOut[] }) {
  return (
    <Collapsible>
      <CollapsibleTrigger className="group/more inline-flex items-center gap-1 rounded-xs text-sm font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
        <ChevronRightIcon className="size-4 transition-transform duration-(--dur-2) group-data-[state=open]/more:rotate-90" aria-hidden="true" />
        Not available ({providers.length})
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 flex flex-col gap-2">
        {providers.map((provider) => (
          <div key={provider.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2">
            <div>
              <p className="text-sm text-foreground">{provider.label}</p>
              <p className="text-xs text-pretty text-muted-foreground">{provider.notes || reasonFor(provider)}</p>
            </div>
            <StatusChip tone="neutral" size="sm">
              {provider.availability === "removed" ? "Removed" : provider.availability === "incompatible" ? "Not available" : "Coming soon"}
            </StatusChip>
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
