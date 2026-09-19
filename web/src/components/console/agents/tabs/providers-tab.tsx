"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useProviders } from "@/components/console/lib/api-hooks";
import { ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

/**
 * "Providers (mode switch; per-slot provider select filtered by kind from
 * `providers.json`; ...)" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE). Both LiveKit
 * Inference and bring-your-own-key providers are selectable for STT/LLM/TTS,
 * both realtime models are selectable, and both image-gen providers are
 * configurable — `ProviderSlotEditor` filters `providers.json` by `kind`
 * only, so every `status="mvp"` entry of that kind is offered.
 */
export function ProvidersTab() {
  const modeLabelId = React.useId();
  const { data, isLoading, isError, error, refetch } = useProviders();
  const { control, watch, formState } = useFormContext<AgentEditorForm>();
  const mode = watch("config.pipeline.mode");
  const pipelineErrors = formState.errors.config?.pipeline;

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorBanner message={`Could not load the provider registry: ${errorMessage(error)}`} onRetry={() => refetch()} />
    );
  }

  const providers = (data?.providers ?? []).filter((p) => p.status === "mvp");

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-4">
        <span id={modeLabelId} className="mb-2 block text-sm font-medium">
          Pipeline mode
        </span>
        <Controller
          control={control}
          name="config.pipeline.mode"
          render={({ field }) => (
            <div role="group" aria-labelledby={modeLabelId} className="inline-flex rounded-lg border border-border p-1">
              <ModeButton active={field.value === "cascaded"} onClick={() => field.onChange("cascaded")}>
                Cascaded (STT + LLM + TTS)
              </ModeButton>
              <ModeButton active={field.value === "realtime"} onClick={() => field.onChange("realtime")}>
                Realtime (speech-to-speech)
              </ModeButton>
            </div>
          )}
        />
        <p className="mt-2 text-xs text-muted-foreground">
          Cascaded chains separate speech-to-text, LLM and text-to-speech providers. Realtime uses one
          speech-to-speech model (Gemini Live, GPT Realtime) and can see camera/screen frames live.
        </p>
      </div>

      {pipelineErrors ? (
        <p className="text-xs text-destructive">
          {[pipelineErrors.stt, pipelineErrors.llm, pipelineErrors.tts, pipelineErrors.realtime]
            .map((e) => e?.message)
            .filter(Boolean)
            .join(" ")}
        </p>
      ) : null}

      {mode === "cascaded" ? (
        <>
          <Controller
            control={control}
            name="config.pipeline.stt"
            render={({ field }) => (
              <ProviderSlotEditor label="Speech-to-text" kind="stt" providers={providers} value={field.value} onChange={field.onChange} />
            )}
          />
          <Controller
            control={control}
            name="config.pipeline.llm"
            render={({ field }) => (
              <ProviderSlotEditor label="LLM" kind="llm" providers={providers} value={field.value} onChange={field.onChange} />
            )}
          />
          <Controller
            control={control}
            name="config.pipeline.tts"
            render={({ field }) => (
              <ProviderSlotEditor label="Text-to-speech" kind="tts" providers={providers} value={field.value} onChange={field.onChange} />
            )}
          />
        </>
      ) : (
        <Controller
          control={control}
          name="config.pipeline.realtime"
          render={({ field }) => (
            <ProviderSlotEditor
              label="Realtime model"
              kind="realtime"
              providers={providers}
              value={field.value}
              onChange={field.onChange}
              helpText="Speech-to-speech model; camera/screen frames are sent live when the model supports video."
            />
          )}
        />
      )}

      <Controller
        control={control}
        name="config.pipeline.avatar"
        render={({ field }) => (
          <ProviderSlotEditor
            label="Avatar"
            kind="avatar"
            providers={providers}
            value={field.value}
            onChange={field.onChange}
            optional
            helpText="Optional talking-head video rendered in the session's stage."
          />
        )}
      />
      <Controller
        control={control}
        name="config.pipeline.image_gen"
        render={({ field }) => (
          <ProviderSlotEditor
            label="Image generation"
            kind="image_gen"
            providers={providers}
            value={field.value}
            onChange={field.onChange}
            optional
            helpText="Optional; used by packs that draw sketches or illustrations (e.g. the insurance notebook)."
          />
        )}
      />

      <details className="group rounded-xl border border-border bg-card p-4">
        <summary className="cursor-pointer text-sm font-semibold">Advanced: workflow LLM</summary>
        <p className="mb-3 mt-1 text-xs text-muted-foreground">
          Used by packs for structured extraction (e.g. claim intake). Defaults to the main LLM in cascaded mode, or a
          LiveKit Inference LLM in realtime mode, when left unset.
        </p>
        <Controller
          control={control}
          name="config.pipeline.workflow_llm"
          render={({ field }) => (
            <ProviderSlotEditor label="Workflow LLM" kind="llm" providers={providers} value={field.value} onChange={field.onChange} optional />
          )}
        />
      </details>
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-3 py-1.5 text-sm font-medium transition-colors outline-none",
        "focus-visible:ring-[3px] focus-visible:ring-ring/50",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
