"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
import { SkeletonRows } from "@/components/shared/loading-state";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useActiveWorkspace } from "./use-settings-queries";
import { api } from "@/lib/api";
import type { ComplianceOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

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

export function ComplianceTab() {
  const { workspace: membership, isLoading: meLoading } = useActiveWorkspace();
  const complianceQuery = useCompliance(membership?.id);
  const queryClient = useQueryClient();

  const [jurisdiction, setJurisdiction] = React.useState<Jurisdiction>("in");
  const [disclosureText, setDisclosureText] = React.useState("");
  const [recordingText, setRecordingText] = React.useState("");
  const [ack, setAck] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
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
      toast.success("Compliance settings updated");
    } catch (err) {
      toast.error(`Couldn't save compliance settings — ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  if (meLoading || complianceQuery.isLoading || !membership) {
    return (
      <Section id="compliance" title="Consent and disclosure">
        <SectionRow>
          <SkeletonRows label="Loading compliance settings" rows={4} rowClassName="h-9" />
        </SectionRow>
      </Section>
    );
  }

  const canManage = canManageCompliance(membership.role);
  const disabled = !canManage || saving;

  return (
    <Section
      id="compliance"
      title="Consent and disclosure"
      description="What callers are told, and asked, before an agent records them or lets them know it's an AI. A starting point, not legal advice — every wording below needs review by counsel for where you operate."
    >
      <SectionRow className="flex flex-col gap-3">
        <span className="text-sm font-medium leading-5">Jurisdiction</span>
        <RadioGroup
          value={jurisdiction}
          onValueChange={(next) => setJurisdiction(next as Jurisdiction)}
          aria-label="Jurisdiction"
          disabled={disabled}
          data-issue-path="settings.compliance.jurisdiction"
          className="grid gap-2.5 sm:grid-cols-3"
        >
          {presets.map((p) => {
            const id = `compliance-jurisdiction-${p.jurisdiction}`;
            const selected = jurisdiction === p.jurisdiction;
            return (
              <label
                key={p.jurisdiction}
                htmlFor={id}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors duration-(--dur-2)",
                  selected ? "border-brand-line bg-brand-soft/40 ring-1 ring-brand-line" : "border-border hover:border-foreground/20",
                  disabled && "cursor-not-allowed opacity-60",
                )}
              >
                <span className="min-w-0 flex-1 text-sm font-medium leading-5">{p.label}</span>
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
            disabled={disabled}
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
            disabled={disabled}
            value={recordingText}
            onChange={(event) => setRecordingText(event.target.value)}
          />
        </Field>
      </SectionRow>

      <SectionRow className="flex flex-col gap-3">
        {preset ? (
          <p className="bg-warning-soft text-warning-text rounded-md px-3 py-2 text-[0.8125rem]">{preset.counsel_note}</p>
        ) : null}
        <label htmlFor="compliance-counsel-ack" className="flex items-start gap-2 text-sm">
          <Checkbox
            id="compliance-counsel-ack"
            checked={ack}
            disabled={disabled}
            onCheckedChange={(checked) => setAck(checked === true)}
            className="mt-0.5"
          />
          I understand this wording is a starting point and needs review by counsel for where this agent operates.
        </label>
      </SectionRow>

      {canManage ? (
        <SectionRow>
          <Button type="button" onClick={() => void onSave()} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </SectionRow>
      ) : null}
    </Section>
  );
}
