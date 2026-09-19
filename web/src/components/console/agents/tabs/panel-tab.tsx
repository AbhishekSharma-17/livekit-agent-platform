"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";

import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { KNOWN_PANEL_IDS } from "@/components/console/lib/constants";
import { useProviders } from "@/components/console/lib/api-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

const CUSTOM = "__custom__";

/**
 * Whether the configured cascaded LLM is known to ignore image parts
 * (DECISIONS-W2 D-W2-10): `false` only when the model is in the provider's
 * suggestion list without `supports_video`; unknown/free-text models and
 * realtime mode never trigger this — mirrors `lkap_contracts.providers.vision_support`.
 */
function isKnownTextOnlyLlm(
  providers: { id: string; models?: { id: string; supports_video?: boolean }[]; default_model?: string | null }[],
  llm: { provider_id?: string; model?: string | null } | null | undefined,
): boolean {
  if (!llm?.provider_id) return false;
  const spec = providers.find((p) => p.id === llm.provider_id);
  if (!spec) return false;
  const modelId = llm.model || spec.default_model;
  const model = spec.models?.find((m) => m.id === modelId);
  return model ? model.supports_video !== true : false;
}

/**
 * "UI panel choice" (task brief) + `CapabilitiesConfig` (docs/CONTRACTS.md
 * §6) — which input affordances the session page offers. Panel ids are
 * offered as a select over the panels shipped in this repo plus free text,
 * since `web/src/panels/registry.ts` (W1-WEB-SESSION, built in parallel)
 * isn't something this package imports — see IMPLEMENTATION_PLAN's
 * parallel-work boundary.
 */
export function PanelTab() {
  const panelSelectId = React.useId();
  const { control, register, watch, setValue } = useFormContext<AgentEditorForm>();
  const panelId = watch("ui_panel_id");
  const isKnown = (KNOWN_PANEL_IDS as readonly string[]).includes(panelId);

  const { data: providersData } = useProviders();
  const mode = watch("config.pipeline.mode");
  const llm = watch("config.pipeline.llm");
  const camera = watch("config.capabilities.camera");
  const screenShare = watch("config.capabilities.screen_share");
  const showVisionHint =
    mode === "cascaded" &&
    (camera || screenShare) &&
    isKnownTextOnlyLlm(providersData?.providers ?? [], llm);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-4">
        <label htmlFor={panelSelectId} className="mb-1 block text-sm font-medium">
          Panel
        </label>
        <div className="flex gap-2">
          <Select
            value={isKnown ? panelId : CUSTOM}
            onValueChange={(next) => {
              if (next !== CUSTOM) setValue("ui_panel_id", next, { shouldDirty: true });
            }}
          >
            <SelectTrigger id={panelSelectId} className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KNOWN_PANEL_IDS.map((id) => (
                <SelectItem key={id} value={id}>
                  {id}
                </SelectItem>
              ))}
              <SelectItem value={CUSTOM}>Custom…</SelectItem>
            </SelectContent>
          </Select>
          {!isKnown ? <Input {...register("ui_panel_id")} placeholder="panel id" className="flex-1" /> : null}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Matches a pack&apos;s <code>ui_panel_id</code>; unrecognised ids fall back to the generic panel.
        </p>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-3 text-sm font-semibold">Session capabilities</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <CapabilityToggle control={control} name="config.capabilities.camera" label="Camera" />
          <CapabilityToggle control={control} name="config.capabilities.screen_share" label="Screen share" />
          <CapabilityToggle control={control} name="config.capabilities.chat_input" label="Chat input" />
          <CapabilityToggle
            control={control}
            name="config.capabilities.vision_inject_per_turn"
            label="Auto-inject latest frame per turn"
          />
        </div>
        {showVisionHint ? (
          <p className="mt-3 text-xs text-amber-600 dark:text-amber-500">
            The selected LLM cannot see images; camera/screen share still reach the UI and{" "}
            <code>pin_frame</code>, but per-turn vision and <code>describe_current_frame</code> are
            disabled. Pick a model marked &quot;vision&quot; (e.g. google/gemini-3.5-flash) to enable it.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function CapabilityToggle({
  control,
  name,
  label,
}: {
  control: ReturnType<typeof useFormContext<AgentEditorForm>>["control"];
  name:
    | "config.capabilities.camera"
    | "config.capabilities.screen_share"
    | "config.capabilities.chat_input"
    | "config.capabilities.vision_inject_per_turn";
  label: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
      <span className="text-sm font-medium">{label}</span>
      <Controller control={control} name={name} render={({ field }) => <Switch checked={field.value} onCheckedChange={field.onChange} />} />
    </div>
  );
}
