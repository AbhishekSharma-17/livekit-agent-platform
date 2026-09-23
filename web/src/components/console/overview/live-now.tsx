"use client";

import Link from "next/link";
import { RadioIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { useAgents } from "@/components/console/lib/api-hooks";

function publicUrl(slug: string): string {
  if (typeof window === "undefined") return `/s/${slug}`;
  return `${window.location.origin}/s/${slug}`;
}

/** docs/UI_UX_SPEC.md §4.1: "Live now": published agents with their public link. */
export function LiveNow() {
  const { data, isLoading } = useAgents();
  const published = (data?.items ?? []).filter((agent) => agent.published);

  return (
    <Section id="live-now" title="Live now">
      {isLoading ? (
        <SectionRow className="text-sm text-muted-foreground">Loading…</SectionRow>
      ) : published.length === 0 ? (
        <SectionRow>
          <EmptyState compact icon={RadioIcon} title="No agents are published" description="Publish one to get a shareable link." />
        </SectionRow>
      ) : (
        published.map((agent) => (
          <SectionRow key={agent.id} className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Link href={`/console/agents/${agent.id}`} className="text-sm font-medium hover:underline">
                {agent.name}
              </Link>
              <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{publicUrl(agent.slug)}</p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <StatusChip tone="live">Live</StatusChip>
              <CopyButton value={publicUrl(agent.slug)} label={`Copy ${agent.name}'s public link`} size="sm" />
            </div>
          </SectionRow>
        ))
      )}
    </Section>
  );
}
