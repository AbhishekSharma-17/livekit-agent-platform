"use client";

/**
 * "Session capabilities" — moved here unchanged from WP-5's `panel-tab.tsx`
 * when the panel composer (V2-11) took over the `panel` section ("Panel &
 * capabilities", which also owns the `capabilities.*` issue paths).
 */
import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";

import { Field } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import { useProviders } from "@/components/console/lib/api-hooks";
import { CAPABILITY_META } from "@/components/console/lib/constants";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

/**
 * Whether the configured cascaded LLM is known to ignore image parts
 * (DECISIONS-W2 D-W2-10): `false` only when the model is in the provider's
 * suggestion list without `supports_video`; unknown/free-text models and
 * realtime mode never trigger this — mirrors `lkap_contracts.providers.vision_support`.
 */
export function isKnownTextOnlyLlm(
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

export function SessionCapabilities() {
  const { control, watch } = useFormContext<AgentEditorForm>();
  const editorCtx = useEditorContext();
  const { data: providersData } = useProviders();
  const mode = watch("config.pipeline.mode");
  const llm = watch("config.pipeline.llm");
  const camera = watch("config.capabilities.camera");
  const screenShare = watch("config.capabilities.screen_share");
  const showVisionHint =
    mode === "cascaded" && (camera || screenShare) && isKnownTextOnlyLlm(providersData?.providers ?? [], llm);

  return (
    <Section
      id="panel-capabilities"
      title="Session capabilities"
      description="Which input affordances the session page offers to callers."
    >
      {(Object.keys(CAPABILITY_META) as Array<keyof typeof CAPABILITY_META>).map((key) => {
        const meta = CAPABILITY_META[key];
        const controlId = `capability-${key}`;
        return (
          <SectionRow key={key}>
            <Field inline label={meta.label} htmlFor={controlId} hint={meta.description}>
              <Controller
                control={control}
                name={`config.capabilities.${key}`}
                render={({ field }) => (
                  <Switch
                    id={controlId}
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    data-issue-path={`capabilities.${key}`}
                  />
                )}
              />
            </Field>
          </SectionRow>
        );
      })}
      {showVisionHint ? (
        <SectionRow>
          <Alert variant="warning">
            <AlertDescription>
              The selected language model can&apos;t see images; camera and screen share still reach the panel, but
              per-turn vision and &quot;Describe current frame&quot; stay off.{" "}
              <Button
                type="button"
                variant="link"
                className="h-auto p-0 text-warning-text underline"
                onClick={() => editorCtx?.goToSection("providers")}
              >
                Pick a model marked &quot;vision&quot; on Providers
              </Button>{" "}
              to turn it on.
            </AlertDescription>
          </Alert>
        </SectionRow>
      ) : null}
    </Section>
  );
}
