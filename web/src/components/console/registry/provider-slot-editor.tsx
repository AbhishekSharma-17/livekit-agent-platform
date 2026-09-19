"use client";

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { ModelCombobox } from "@/components/console/registry/model-combobox";
import { defaultFieldValues, RegistryForm } from "@/components/console/registry/registry-form";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

const NONE = "__none__";

/**
 * One pipeline slot (stt/llm/tts/realtime/avatar/image_gen): provider
 * select filtered by kind, then the registry-generated model combobox,
 * credential picker and field form for whichever provider is chosen. This
 * is the unit `providers-tab.tsx` repeats per slot — see
 * docs/CONTRACTS.md §4/§6.
 */
export function ProviderSlotEditor({
  label,
  kind,
  providers,
  value,
  onChange,
  optional = false,
  helpText,
}: {
  label: string;
  kind: ProviderSpec["kind"];
  providers: ProviderSpec[];
  value: ProviderRef | null | undefined;
  onChange: (next: ProviderRef | null) => void;
  optional?: boolean;
  helpText?: string;
}) {
  const modelId = React.useId();
  const options = providers.filter((p) => p.kind === kind && p.status === "mvp");
  const selectedSpec = value ? options.find((p) => p.id === value.provider_id) : undefined;

  function handleProviderChange(providerId: string) {
    if (providerId === NONE) {
      onChange(null);
      return;
    }
    const spec = options.find((p) => p.id === providerId);
    if (!spec) return;
    onChange({
      provider_id: spec.id,
      credential_id: null,
      model: spec.default_model ?? null,
      fields: defaultFieldValues(spec.fields ?? []),
    });
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">
            {label}
            {!optional ? <span className="ml-0.5 text-destructive">*</span> : null}
          </h3>
          {helpText ? <p className="text-xs text-muted-foreground">{helpText}</p> : null}
        </div>
        {selectedSpec ? (
          <div className="flex gap-1">
            {selectedSpec.capabilities?.video_input ? <Badge variant="secondary">video</Badge> : null}
            {selectedSpec.capabilities?.silent_tool_reply ? <Badge variant="secondary">silent tools</Badge> : null}
            {selectedSpec.requires_credential ? null : <Badge variant="secondary">no key needed</Badge>}
          </div>
        ) : null}
      </div>

      <Select value={value?.provider_id ?? NONE} onValueChange={handleProviderChange}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder={`Choose a ${label.toLowerCase()} provider`} />
        </SelectTrigger>
        <SelectContent>
          {optional ? <SelectItem value={NONE}>None</SelectItem> : null}
          {options.map((option) => (
            <SelectItem key={option.id} value={option.id}>
              {option.label} — {option.vendor}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {selectedSpec && value ? (
        <div className="mt-4 space-y-4">
          {(selectedSpec.models ?? []).length > 0 ? (
            <div>
              <label htmlFor={modelId} className="mb-1 block text-sm font-medium">
                Model
              </label>
              <ModelCombobox
                id={modelId}
                models={selectedSpec.models ?? []}
                value={value.model ?? ""}
                placeholder={selectedSpec.default_model ? `${selectedSpec.default_model} (default)` : "Model id"}
                onChange={(model) => onChange({ ...value, model: model.trim() === "" ? null : model })}
              />
            </div>
          ) : null}

          {selectedSpec.requires_credential ? (
            <CredentialPicker
              spec={selectedSpec}
              value={value.credential_id}
              onChange={(credentialId) => onChange({ ...value, credential_id: credentialId })}
            />
          ) : null}

          <RegistryForm
            fields={selectedSpec.fields ?? []}
            values={value.fields ?? {}}
            onChange={(name, fieldValue) =>
              onChange({ ...value, fields: { ...(value.fields ?? {}), [name]: fieldValue } })
            }
            idPrefix={`slot-${label}`}
          />
        </div>
      ) : null}

      {options.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">No {label.toLowerCase()} providers in the registry.</p>
      ) : null}
    </div>
  );
}
