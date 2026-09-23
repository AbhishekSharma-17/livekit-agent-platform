"use client";

import { DescriptionList } from "@/components/shared/description-list";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { CopyButton } from "@/components/shared/copy-button";
import { useHealth } from "@/components/console/lib/api-hooks";
import { SkeletonRows } from "@/components/shared/loading-state";

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** docs/UI_UX_SPEC.md §4.11: "Environment (read-only from /v1/health)". */
export function EnvironmentTab() {
  const { data: health, isLoading } = useHealth();

  if (isLoading || !health) {
    return (
      <Section id="environment" title="Environment">
        <SectionRow>
          <SkeletonRows label="Loading environment" rows={3} rowClassName="h-6" />
        </SectionRow>
      </Section>
    );
  }

  const diagnostics = JSON.stringify(health, null, 2);

  return (
    <Section
      id="environment"
      title="Environment"
      aside={<CopyButton value={diagnostics} label="Copy diagnostics" size="sm" />}
    >
      <SectionRow>
        <DescriptionList
          columns={2}
          items={[
            { term: "Version", detail: health.version, mono: true },
            { term: "LiveKit URL", detail: hostOf(health.livekit_url), mono: true },
            {
              term: "Database",
              detail: <StatusChip tone={health.db === "ok" ? "success" : "danger"}>{health.db}</StatusChip>,
            },
            { term: "API reachable", detail: <StatusChip tone={health.ok ? "success" : "danger"}>{health.ok ? "Reachable" : "Unreachable"}</StatusChip> },
            { term: "Packs loaded", detail: health.packs.join(", ") || "None" },
          ]}
        />
      </SectionRow>
    </Section>
  );
}
