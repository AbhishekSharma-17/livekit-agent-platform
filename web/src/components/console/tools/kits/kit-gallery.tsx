"use client";

import * as React from "react";
import { PackageIcon } from "lucide-react";

import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { NoMatches } from "@/components/shared/empty-state";
import { Tag, TagList } from "@/components/shared/tag";
import { Icon } from "@/components/shared/icon";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useToolKits } from "@/components/console/lib/api-hooks";
import { AddKitDialog } from "@/components/console/tools/kits/add-kit-dialog";
import { Highlight, ListToolbar, SEARCH_THRESHOLD, matchesQuery } from "@/components/console/tools/list-search";
import { useWriteGate } from "@/components/console/tools/write-gate";
import type { AgentOut, ToolKit } from "@/contracts/lkap-contracts";

/** What a kit's default variant needs before it can run, in plain words. */
function kitNeeds(kit: ToolKit): string[] {
  // `ToolKit.variant()` is a Python-only helper (`lkap_contracts.kits`); the console re-does
  // its one line (default variant id, else the given one) since the JSON response is data only.
  const variant = kit.variants.find((item) => item.id === kit.default_variant) ?? kit.variants[0];
  const needs: string[] = [];
  if ((variant?.requires?.secret_names ?? []).length > 0) needs.push("a key");
  if ((variant?.requires?.apps ?? []).length > 0) needs.push("a connected app");
  if (variant?.requires?.dataset) needs.push("a lookup table");
  if (kit.variants.length > 1) needs.push(`${kit.variants.length} ways to run it`);
  return needs;
}

/**
 * "Kits" (V6-19, D-V6-26; ask #142): ready-made bundles for a common job (look a record up,
 * open a case, take down details, verify the caller, send a payment link, hand over to the
 * team, log the call, book appointments) — one card per catalogue kit (`GET /v1/tool-kits`),
 * each opening the Add-kit dialog. Sits on the agent's Tools tab, above the plain tool
 * sections: a kit is usually the faster way to add one of these jobs, and its own dialog is
 * where the admin still gets to see (and refuse) exactly what it will add. Searchable once
 * the catalogue has six or more kits (docs/ui/DESIGN-SYSTEM.md section 9).
 */
export function KitGallery({ agent }: { agent: AgentOut }) {
  const kitsQuery = useToolKits();
  const [query, setQuery] = React.useState("");
  const kits = kitsQuery.data?.items ?? [];
  const visible = kits.filter((kit) => matchesQuery([kit.name, kit.summary, ...kitNeeds(kit)], query));

  return (
    <Section id="tools-kits" title="Kits" description="A whole job in one step — tools, panel and instructions together.">
      <SectionRow>
        {kitsQuery.isLoading ? (
          <LoadingRegion label="Loading kits" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <KitCardSkeleton key={i} />
            ))}
          </LoadingRegion>
        ) : kitsQuery.isError ? (
          <ErrorBanner error={kitsQuery.error} context={{ action: "load kits" }} onRetry={() => kitsQuery.refetch()} />
        ) : kits.length === 0 ? (
          <EmptyState compact icon={PackageIcon} title="No kits available" description="Nothing in the catalogue yet." />
        ) : (
          <>
            {kits.length >= SEARCH_THRESHOLD || query !== "" ? (
              <ListToolbar items="kits" query={query} onQueryChange={setQuery} />
            ) : null}
            {visible.length === 0 ? (
              <NoMatches items="kits" query={query} onClear={() => setQuery("")} />
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {visible.map((kit) => (
                  <KitCard key={kit.id} kit={kit} agent={agent} query={query} />
                ))}
              </div>
            )}
          </>
        )}
      </SectionRow>
    </Section>
  );
}

/** Mirrors `KitCard`: title and summary, the needs tags, then the action. */
function KitCardSkeleton() {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-3/5" />
      </div>
      <div className="flex gap-1.5">
        <Skeleton className="h-[22px] w-14 rounded-sm" />
        <Skeleton className="h-[22px] w-24 rounded-sm" />
      </div>
      <Skeleton className="h-7 w-32" />
    </div>
  );
}

function KitCard({ kit, agent, query }: { kit: ToolKit; agent: AgentOut; query: string }) {
  const needs = kitNeeds(kit);
  const gate = useWriteGate();

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-body font-semibold text-foreground">
            <Highlight text={kit.name} query={query} />
          </h3>
          <p className="text-label text-pretty text-text-secondary">
            <Highlight text={kit.summary} query={query} />
          </p>
        </div>
        <Icon as={PackageIcon} className="shrink-0 text-text-tertiary" />
      </div>
      {needs.length > 0 ? (
        <TagList aria-label="Needs">
          {needs.map((need) => (
            <Tag key={need}>{need}</Tag>
          ))}
        </TagList>
      ) : null}
      {gate.show ? (
        <AddKitDialog
          kit={kit}
          agent={agent}
          trigger={
            <Button type="button" variant="secondary" size="sm" className="mt-auto w-fit" disabled={gate.pending}>
              Add to this agent
            </Button>
          }
        />
      ) : null}
    </div>
  );
}
