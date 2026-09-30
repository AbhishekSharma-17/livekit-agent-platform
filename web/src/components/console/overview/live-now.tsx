"use client";

import Link from "next/link";
import { RadioIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { SkeletonRows } from "@/components/shared/loading-state";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { useAgents } from "@/components/console/lib/api-hooks";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { liveAgents } from "./overview-stats";

function publicUrl(slug: string): string {
  if (typeof window === "undefined") return `/s/${slug}`;
  return `${window.location.origin}/s/${slug}`;
}

/** "Live now": published, unarchived agents with their public link to copy. */
export function LiveNow() {
  const { data, isLoading, isError, error, refetch } = useAgents();
  const published = liveAgents(data?.items ?? []);

  return (
    <Section id="live-now" title="Live now">
      {isLoading ? (
        <SectionRow>
          <SkeletonRows label="Loading live agents" rows={2} rowClassName="h-9" />
        </SectionRow>
      ) : isError ? (
        <SectionRow>
          <ErrorBanner error={error} context={{ action: "load live agents" }} onRetry={() => void refetch()} />
        </SectionRow>
      ) : published.length === 0 ? (
        <SectionRow>
          <EmptyState compact icon={RadioIcon} title="No agents are published" description="Publish one to get a shareable link." />
        </SectionRow>
      ) : (
        published.map((agent) => (
          <SectionRow key={agent.id} className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Link href={`/console/agents/${agent.id}`} className="text-body font-medium text-foreground hover:underline">
                {agent.name}
              </Link>
              <p className="mt-0.5 truncate font-mono text-caption text-text-secondary" title={publicUrl(agent.slug)}>
                {publicUrl(agent.slug)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <StatusPill tone="live" size="sm">
                Live
              </StatusPill>
              <CopyButton value={publicUrl(agent.slug)} label={`Copy ${agent.name}'s public link`} size="sm" />
            </div>
          </SectionRow>
        ))
      )}
    </Section>
  );
}
