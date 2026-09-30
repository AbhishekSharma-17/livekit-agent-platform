"use client";

import { DescriptionList } from "@/components/shared/description-list";
import { Section, SectionRow } from "@/components/shared/section";
import { LifecycleBadge, StatusPill } from "@/components/shared/status-chip";
import { CopyButton } from "@/components/shared/copy-button";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useHealth } from "@/components/console/lib/api-hooks";
import { FormSkeleton } from "./settings-card";

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const DESCRIPTION = "Read-only details of the API this console talks to.";

/** docs/UI_UX_SPEC.md §4.11: "Environment (read-only from /v1/health)". */
export function EnvironmentTab() {
  const { data: health, isLoading, isError, error, refetch } = useHealth();

  if (isLoading) {
    return (
      <Section id="environment" title="Environment" description={DESCRIPTION}>
        <SectionRow>
          <FormSkeleton label="Loading environment" fields={4} columns={2} className="max-w-none" />
        </SectionRow>
      </Section>
    );
  }

  if (isError || !health) {
    // Unavailable: the API didn't answer. Say so, and what to try.
    return (
      <Section id="environment" title="Environment" description={DESCRIPTION}>
        <SectionRow>
          <ErrorBanner
            error={error ?? new Error("The API didn't answer.")}
            context={{ action: "reach the API" }}
            onRetry={() => void refetch()}
          />
        </SectionRow>
      </Section>
    );
  }

  const diagnostics = JSON.stringify(health, null, 2);

  return (
    <Section
      id="environment"
      title="Environment"
      description={DESCRIPTION}
      aside={<CopyButton value={diagnostics} label="Copy diagnostics" size="sm" />}
    >
      <SectionRow>
        <DescriptionList
          columns={2}
          items={[
            { term: "Version", detail: health.version, mono: true },
            { term: "LiveKit URL", detail: hostOf(health.livekit_url), mono: true },
            { term: "Database", detail: <LifecycleBadge state={health.db} size="sm" /> },
            {
              term: "API reachable",
              detail: (
                <StatusPill tone={health.ok ? "success" : "danger"} size="sm">
                  {health.ok ? "Reachable" : "Unreachable"}
                </StatusPill>
              ),
            },
            { term: "Packs loaded", detail: health.packs.join(", ") || "None" },
          ]}
        />
      </SectionRow>
    </Section>
  );
}
