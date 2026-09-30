"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { CheckboxRow } from "@/components/shared/choice";
import { MetaList } from "@/components/shared/data-display";
import { Field } from "@/components/shared/field";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { Section, SectionRow } from "@/components/shared/section";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { useActiveWorkspace } from "./use-settings-queries";
import { FieldSkeleton, SettingsCardFooter } from "./settings-card";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ComplianceOut } from "@/contracts/lkap-contracts";

/** `lkap_contracts.compliance.Jurisdiction` — the generated TS inlines it, so it's not an exported name. */
type Jurisdiction = NonNullable<ComplianceOut["settings"]["jurisdiction"]>;

/**
 * Settings → Compliance (V5-17, PLAN-V5 V5-15/V5-17, D-V5-22): the
 * workspace's jurisdiction and its AI disclosure / recording wording
 * (`GET`/`PUT /v1/workspaces/{id}` + `.../compliance`,
 * `lkap_contracts.compliance`). The three presets are served by the api
 * (`ComplianceOut.presets`), never hand-copied here (`docs/v5/_asks.md`
 * #86's concern) — each carries its own "confirm with counsel" note, since
 * none of this is legal advice.
 */

/** `admin`/`owner` may change compliance settings, same rule as the Workspace tab. */
function canManageCompliance(role: string | undefined): boolean {
  return role === "admin" || role === "owner";
}

function useCompliance(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["settings", "compliance", workspaceId] as const,
    queryFn: () => api.get<ComplianceOut>(`workspaces/${workspaceId}/compliance`),
    enabled: Boolean(workspaceId),
  });
}

const TITLE = "Consent and disclosure";
const DESCRIPTION =
  "What callers are told, and asked, before an agent records them or tells them it's an AI. A starting point, not legal advice: have counsel review every wording for where you operate.";

export function ComplianceTab() {
  const { workspace: membership, isLoading: meLoading } = useActiveWorkspace();
  const complianceQuery = useCompliance(membership?.id);
  const queryClient = useQueryClient();

  const [jurisdiction, setJurisdiction] = React.useState<Jurisdiction>("in");
  const [disclosureText, setDisclosureText] = React.useState("");
  const [recordingText, setRecordingText] = React.useState("");
  const [ack, setAck] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<unknown>(null);
  const [initialized, setInitialized] = React.useState(false);

  const compliance = complianceQuery.data;
  React.useEffect(() => {
    if (compliance && !initialized) {
      setJurisdiction(compliance.settings.jurisdiction ?? "in");
      setDisclosureText(compliance.settings.disclosure_text ?? "");
      setRecordingText(compliance.settings.recording_text ?? "");
      setAck(compliance.settings.counsel_note_ack ?? false);
      setInitialized(true);
    }
  }, [compliance, initialized]);

  const presets = compliance?.presets ?? [];
  const preset = presets.find((p) => p.jurisdiction === jurisdiction) ?? presets[0];

  async function onSave() {
    if (!membership) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.put(`workspaces/${membership.id}`, {
        settings: {
          compliance: {
            jurisdiction,
            disclosure_text: disclosureText.trim() === "" ? null : disclosureText,
            recording_text: recordingText.trim() === "" ? null : recordingText,
            counsel_note_ack: ack,
          },
        },
      });
      queryClient.invalidateQueries({ queryKey: ["settings", "compliance", membership.id] });
      toast.success("Consent and disclosure saved");
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  if (meLoading || complianceQuery.isLoading) {
    return (
      <Section id="compliance" title={TITLE} description={DESCRIPTION}>
        <SectionRow>
          <LoadingRegion label="Loading consent and disclosure settings" className="flex flex-col gap-4">
            <div className="grid gap-2.5 sm:grid-cols-3">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-12 w-full rounded-lg" />
              ))}
            </div>
            <FieldSkeleton tall />
            <FieldSkeleton tall />
          </LoadingRegion>
        </SectionRow>
      </Section>
    );
  }

  if (complianceQuery.isError || !membership || !compliance) {
    return (
      <Section id="compliance" title={TITLE} description={DESCRIPTION}>
        <SectionRow>
          <ErrorBanner
            error={complianceQuery.error ?? new Error("These settings aren't available.")}
            context={{ action: "load consent and disclosure settings" }}
            onRetry={() => void complianceQuery.refetch()}
          />
        </SectionRow>
      </Section>
    );
  }

  if (!canManageCompliance(membership.role)) {
    return <ComplianceReadOnly compliance={compliance} />;
  }

  return (
    <Section id="compliance" title={TITLE} description={DESCRIPTION}>
      <div className="flex flex-col divide-y divide-border">
        {saveError ? (
          <SectionRow>
            <ErrorBanner error={saveError} context={{ action: "save consent and disclosure settings" }} />
          </SectionRow>
        ) : null}
        <SectionRow className="flex flex-col gap-3">
          <span id="compliance-jurisdiction-label" className="text-label font-medium text-foreground">
            Jurisdiction
          </span>
          <RadioGroup
            value={jurisdiction}
            onValueChange={(next) => setJurisdiction(next as Jurisdiction)}
            aria-labelledby="compliance-jurisdiction-label"
            disabled={saving}
            data-issue-path="settings.compliance.jurisdiction"
            className="grid gap-2.5 sm:grid-cols-3"
          >
            {presets.map((p) => {
              const id = `compliance-jurisdiction-${p.jurisdiction}`;
              return (
                <label
                  key={p.jurisdiction}
                  htmlFor={id}
                  className="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 transition-colors duration-(--duration-fast) hover:bg-muted has-data-[state=checked]:border-brand-border has-data-[state=checked]:bg-brand-subtle has-disabled:cursor-not-allowed has-disabled:opacity-50"
                >
                  <span className="min-w-0 flex-1 text-control leading-5 font-medium text-foreground">{p.label}</span>
                  <RadioGroupItem id={id} value={p.jurisdiction} className="mt-0.5" />
                </label>
              );
            })}
          </RadioGroup>
        </SectionRow>

        <SectionRow>
          <Field
            label="AI disclosure line"
            htmlFor="compliance-disclosure-text"
            optional
            hint={preset ? `Leave empty to use ${preset.label}'s wording: "${preset.disclosure_text}"` : undefined}
          >
            <Textarea
              id="compliance-disclosure-text"
              rows={2}
              maxLength={2000}
              disabled={saving}
              value={disclosureText}
              onChange={(event) => setDisclosureText(event.target.value)}
            />
          </Field>
        </SectionRow>

        <SectionRow>
          <Field
            label="Recording question"
            htmlFor="compliance-recording-text"
            optional
            hint={preset ? `Leave empty to use ${preset.label}'s wording: "${preset.recording_text}"` : undefined}
          >
            <Textarea
              id="compliance-recording-text"
              rows={2}
              maxLength={2000}
              disabled={saving}
              value={recordingText}
              onChange={(event) => setRecordingText(event.target.value)}
            />
          </Field>
        </SectionRow>

        <SectionRow className="flex flex-col gap-3">
          {preset ? <Alert tone="warning">{preset.counsel_note}</Alert> : null}
          <CheckboxRow
            id="compliance-counsel-ack"
            checked={ack}
            disabled={saving}
            onChange={(event) => setAck(event.target.checked)}
            label="I understand this wording is a starting point and needs review by counsel for where this agent operates."
          />
        </SectionRow>

        <SettingsCardFooter>
          <Button type="button" variant="primary" busy={saving} busyLabel="Saving…" onClick={() => void onSave()}>
            Save
          </Button>
        </SettingsCardFooter>
      </div>
    </Section>
  );
}

/** What a viewer or builder sees: the wording callers actually hear, and who can change it. */
function ComplianceReadOnly({ compliance }: { compliance: ComplianceOut }) {
  const { jurisdiction, disclosure_text: disclosure, recording_text: recording } = compliance.effective;
  const preset = compliance.presets.find((p) => p.jurisdiction === jurisdiction);
  return (
    <Section id="compliance" title={TITLE} description={DESCRIPTION}>
      <SectionRow>
        <MetaList
          items={[
            { term: "Jurisdiction", value: preset?.label ?? jurisdiction ?? "—" },
            { term: "AI disclosure line", value: disclosure ?? "—" },
            { term: "Recording question", value: recording ?? "—" },
            {
              term: "Counsel review",
              value: compliance.settings.counsel_note_ack ? "Acknowledged" : "Not acknowledged yet",
            },
          ]}
        />
      </SectionRow>
      <SectionRow>
        <ReadOnlyNote variant="block">{readOnlyCopy("admin", "change what callers are told")}</ReadOnlyNote>
      </SectionRow>
    </Section>
  );
}
