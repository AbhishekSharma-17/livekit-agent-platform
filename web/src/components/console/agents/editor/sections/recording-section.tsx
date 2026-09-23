"use client";

import { Controller, useFormContext } from "react-hook-form";

import { Field } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { Switch } from "@/components/ui/switch";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

import { describedBy } from "./field-aria";

/**
 * Recording section (UI_UX_SPEC-V2-AMENDMENTS §2.3): switch, audio-only
 * (fixed on in Phase 1), storage config, retention. Edits
 * `config.recording` (`RecordingConfig`, CONTRACTS-V2 §4.3). Storage configs
 * have no api yet, so the connection/workspace default is shown read-only.
 */
export function RecordingSection() {
  const { control, watch, formState } = useFormContext<AgentEditorForm>();
  const enabled = watch("config.recording.enabled");
  const retentionError = formState.errors.config?.recording?.retention_days?.message;

  return (
    <Section
      id="recording-settings"
      title="Recording"
      description="Record calls through LiveKit Egress and play them back from the session's page."
    >
      <SectionRow>
        <Field
          inline
          label="Record calls"
          htmlFor="recording-enabled"
          hint="Starts when the agent joins and stops when the call ends."
        >
          <Controller
            control={control}
            name="config.recording.enabled"
            render={({ field }) => (
              <Switch
                id="recording-enabled"
                checked={field.value}
                onCheckedChange={field.onChange}
                aria-describedby={describedBy("recording-enabled")}
              />
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          inline
          label="Audio only"
          htmlFor="recording-audio-only"
          hint="Calls are recorded as audio. Video recording isn't supported in this release."
        >
          <Controller
            control={control}
            name="config.recording.audio_only"
            render={({ field }) => (
              <Switch
                id="recording-audio-only"
                checked={field.value}
                disabled
                aria-describedby={describedBy("recording-audio-only")}
              />
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          label="Storage"
          htmlFor="recording-storage"
          hint="Recordings go to the storage set on the agent's connection, or the workspace default."
        >
          <InputGroup className="max-w-sm" data-disabled="true">
            <InputGroupInput
              id="recording-storage"
              value="Default storage"
              readOnly
              disabled
              aria-describedby={describedBy("recording-storage")}
            />
          </InputGroup>
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          label="Keep recordings for"
          htmlFor="recording-retention"
          optional
          hint="Leave empty to keep recordings until they're deleted."
          error={retentionError}
        >
          <Controller
            control={control}
            name="config.recording.retention_days"
            render={({ field }) => (
              <InputGroup className="max-w-48">
                <InputGroupInput
                  id="recording-retention"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  disabled={!enabled}
                  value={field.value ?? ""}
                  onBlur={field.onBlur}
                  onChange={(event) =>
                    field.onChange(event.target.value === "" ? null : Number(event.target.value))
                  }
                  aria-invalid={retentionError ? true : undefined}
                  aria-describedby={describedBy("recording-retention", Boolean(retentionError))}
                  data-issue-path="recording.retention_days"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText>days</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
            )}
          />
        </Field>
      </SectionRow>
    </Section>
  );
}
