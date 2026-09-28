"use client";

import * as React from "react";
import { PackageIcon } from "lucide-react";

import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/shared/status-chip";
import { Icon } from "@/components/shared/icon";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useToolKits } from "@/components/console/lib/api-hooks";
import { AddKitDialog } from "@/components/console/tools/kits/add-kit-dialog";
import type { AgentOut, ToolKit } from "@/contracts/lkap-contracts";

/**
 * "Kits" (V6-19, D-V6-26; ask #142): ready-made bundles for a common job (look a record up,
 * open a case, take down details, verify the caller, send a payment link, hand over to the
 * team, log the call, book appointments) — one card per catalogue kit (`GET /v1/tool-kits`),
 * each opening the Add-kit dialog. Sits on the agent's Tools tab, above the plain tool
 * sections: a kit is usually the faster way to add one of these jobs, and its own dialog is
 * where the admin still gets to see (and refuse) exactly what it will add.
 */
export function KitGallery({ agent }: { agent: AgentOut }) {
  const kitsQuery = useToolKits();

  return (
    <Section id="tools-kits" title="Kits" description="A whole job in one step — tools, panel and instructions together.">
      <SectionRow>
        {kitsQuery.isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-32 w-full" />
            ))}
          </div>
        ) : kitsQuery.isError ? (
          <ErrorBanner message={`Couldn't load kits — ${errorMessage(kitsQuery.error)}`} onRetry={() => kitsQuery.refetch()} />
        ) : (kitsQuery.data?.items ?? []).length === 0 ? (
          <EmptyState compact icon={PackageIcon} title="No kits available" description="Nothing in the catalogue yet." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(kitsQuery.data?.items ?? []).map((kit) => (
              <KitCard key={kit.id} kit={kit} agent={agent} />
            ))}
          </div>
        )}
      </SectionRow>
    </Section>
  );
}

function KitCard({ kit, agent }: { kit: ToolKit; agent: AgentOut }) {
  // `ToolKit.variant()` is a Python-only helper (`lkap_contracts.kits`); the console re-does
  // its one line (default variant id, else the given one) since the JSON response is data only.
  const variant = kit.variants.find((item) => item.id === kit.default_variant) ?? kit.variants[0];
  const needs: string[] = [];
  if ((variant?.requires?.secret_names ?? []).length > 0) needs.push("a key");
  if ((variant?.requires?.apps ?? []).length > 0) needs.push("a connected app");
  if (variant?.requires?.dataset) needs.push("a lookup table");
  if (kit.variants.length > 1) needs.push(`${kit.variants.length} ways to run it`);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-foreground">{kit.name}</h3>
          <p className="text-[0.8125rem] text-pretty text-muted-foreground">{kit.summary}</p>
        </div>
        <Icon as={PackageIcon} className="shrink-0 text-muted-foreground" />
      </div>
      {needs.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {needs.map((need) => (
            <StatusChip key={need} tone="neutral" size="sm">
              {need}
            </StatusChip>
          ))}
        </div>
      ) : null}
      <AddKitDialog
        kit={kit}
        agent={agent}
        trigger={
          <Button type="button" variant="outline" size="sm" className="mt-auto w-fit">
            Add to this agent
          </Button>
        }
      />
    </div>
  );
}
