"use client";

import * as React from "react";

import { useDeclareModel } from "@/components/console/lib/api-hooks";
import { useWriteAccess } from "@/components/console/lib/roles";
import { errorMessage } from "@/components/console/shared/error-banner";
import { cn } from "@/lib/utils";
import type { CatalogItem, ModelCapabilities, ProviderModelOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * "This model can…" (docs/v4/CUSTOM-MODELS.md D-V4-24, V4-09): what a custom
 * model can do, and where that answer comes from. Admins declare it (a
 * declaration wins over everything else); everyone else sees the resolved
 * answer with its source.
 */

export type CapabilityKey = "vision" | "tools";
export type CapabilitySource = "declared" | "detected" | "catalog" | "registry";

export interface ResolvedCapability {
  value: boolean | null;
  source: CapabilitySource | null;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function listIncludes(value: unknown, needle: string): boolean {
  return Array.isArray(value) && value.some((entry) => entry === needle);
}

/** Whether a catalog item's vendor metadata says it takes image input (OpenRouter nests it under `architecture`). */
export function catalogSaysVision(meta: Record<string, unknown> | undefined): boolean | null {
  if (!meta) return null;
  const modalities = asRecord(meta.architecture)?.input_modalities ?? meta.input_modalities ?? meta.inputModalities;
  if (Array.isArray(modalities)) return modalities.some((m) => typeof m === "string" && m.toLowerCase() === "image");
  const anthropic = asRecord(asRecord(meta.capabilities)?.image_input)?.supported;
  if (typeof anthropic === "boolean") return anthropic;
  const mistral = asRecord(meta.capabilities)?.vision;
  if (typeof mistral === "boolean") return mistral;
  return null;
}

/** Whether a catalog item's vendor metadata says it calls tools. */
export function catalogSaysTools(meta: Record<string, unknown> | undefined): boolean | null {
  if (!meta) return null;
  if (Array.isArray(meta.supported_parameters)) return listIncludes(meta.supported_parameters, "tools");
  const mistral = asRecord(meta.capabilities)?.function_calling;
  if (typeof mistral === "boolean") return mistral;
  if (typeof meta.supportsTools === "boolean") return meta.supportsTools;
  return null;
}

/**
 * The console's reading of the api's resolution order (R-V4-23): declared →
 * detected by the last test → the vendor's live list → the registry → unknown.
 */
export function resolveCapability(
  key: CapabilityKey,
  {
    record,
    catalogItem,
    spec,
    modelId,
  }: {
    record: ProviderModelOut | null | undefined;
    catalogItem?: CatalogItem | null;
    spec: ProviderSpec;
    modelId: string;
  },
): ResolvedCapability {
  const declared = record?.declared?.[key];
  if (typeof declared === "boolean") return { value: declared, source: "declared" };
  const detected = record?.detected?.[key];
  if (typeof detected === "boolean") return { value: detected, source: "detected" };
  const fromCatalog = key === "vision" ? catalogSaysVision(catalogItem?.meta) : catalogSaysTools(catalogItem?.meta);
  if (fromCatalog !== null) return { value: fromCatalog, source: "catalog" };
  if (key === "vision") {
    const listed = (spec.models ?? []).find((m) => m.id === modelId);
    if (listed) return { value: listed.supports_video === true, source: "registry" };
  } else if (typeof spec.capabilities?.tool_calling === "boolean") {
    return { value: spec.capabilities.tool_calling, source: "registry" };
  }
  return { value: null, source: null };
}

export function sourceLabel(source: CapabilitySource | null, vendor: string): string {
  switch (source) {
    case "declared":
      return "set by an admin";
    case "detected":
      return "found by the last test";
    case "catalog":
      return `from ${vendor}'s catalog`;
    case "registry":
      return "from the provider's defaults";
    default:
      return "not known yet";
  }
}

const ROWS: { key: CapabilityKey; label: string; hint: string }[] = [
  { key: "vision", label: "See images", hint: "Camera and screen-share frames are sent only to models that can see." },
  { key: "tools", label: "Call tools", hint: "Tools, knowledge search and flow steps need tool calls." },
];

type Choice = "yes" | "no" | "unset";

function choiceOf(value: boolean | null | undefined): Choice {
  return value === true ? "yes" : value === false ? "no" : "unset";
}

export interface ModelCapabilitiesProps {
  spec: ProviderSpec;
  modelId: string;
  record: ProviderModelOut | null | undefined;
  catalogItem?: CatalogItem | null;
  /** A fresh test result's findings, until the record refetches. */
  detected?: ModelCapabilities | null;
  className?: string;
}

/** The "This model can…" block under a custom model id (admins edit; others read). */
export function ModelCapabilitiesBlock({ spec, modelId, record, catalogItem, detected, className }: ModelCapabilitiesProps) {
  const { canWrite: isAdmin } = useWriteAccess("admin");
  const declare = useDeclareModel(spec.id);
  const headingId = React.useId();
  const effectiveRecord: ProviderModelOut | null | undefined =
    detected && record ? { ...record, detected: { ...(record.detected ?? {}), ...detected } } : detected ? ({ detected } as ProviderModelOut) : record;

  function setDeclared(key: CapabilityKey, choice: Choice) {
    const next: ModelCapabilities = { ...(record?.declared ?? {}) };
    next[key] = choice === "unset" ? null : choice === "yes";
    delete next.source;
    declare.mutate({ modelId, declared: next });
  }

  return (
    <section
      aria-labelledby={headingId}
      data-slot="model-capabilities"
      className={cn("flex flex-col gap-2.5 rounded-md border border-border bg-muted/30 px-3 py-3", className)}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <h4 id={headingId} className="text-[0.8125rem] font-semibold text-foreground">
          This model can…
        </h4>
        <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
          {declare.isPending ? "Saving…" : declare.isError ? "" : declare.isSuccess ? "Saved" : ""}
        </span>
      </div>
      <ul className="flex flex-col gap-3">
        {ROWS.map((row) => {
          const resolved = resolveCapability(row.key, { record: effectiveRecord, catalogItem, spec, modelId });
          const declaredChoice = choiceOf(record?.declared?.[row.key]);
          const answer = resolved.value === true ? "Yes" : resolved.value === false ? "No" : "Unknown";
          return (
            <li key={row.key} className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[0.8125rem] font-medium text-foreground">{row.label}</span>
                <span className="text-xs text-pretty text-muted-foreground" data-testid={`capability-${row.key}-source`}>
                  {answer} · {sourceLabel(resolved.source, spec.vendor)}
                </span>
              </div>
              {isAdmin ? (
                <DeclareChoice
                  name={`${headingId}-${row.key}`}
                  label={row.label}
                  value={declaredChoice}
                  disabled={declare.isPending}
                  onChange={(choice) => setDeclared(row.key, choice)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      {declare.isError ? (
        <p className="text-xs text-danger-text">Couldn&apos;t save: {errorMessage(declare.error)}</p>
      ) : null}
      {isAdmin ? (
        <p className="text-xs text-muted-foreground">Your answer overrides the test and the vendor&apos;s list. &ldquo;Auto&rdquo; goes back to them.</p>
      ) : null}
    </section>
  );
}

function DeclareChoice({
  name,
  label,
  value,
  disabled,
  onChange,
}: {
  name: string;
  label: string;
  value: Choice;
  disabled?: boolean;
  onChange: (choice: Choice) => void;
}) {
  const options: { value: Choice; text: string }[] = [
    { value: "unset", text: "Auto" },
    { value: "yes", text: "Yes" },
    { value: "no", text: "No" },
  ];
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex shrink-0 self-start rounded-md border border-border bg-background p-0.5"
    >
      {options.map((option) => (
        <label
          key={option.value}
          className={cn(
            "relative cursor-pointer rounded-sm px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors duration-(--dur-2)",
            "hover:text-foreground has-[:checked]:bg-brand-soft has-[:checked]:text-brand-text",
            "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
            disabled && "pointer-events-none opacity-60",
          )}
        >
          <input
            type="radio"
            className="sr-only"
            name={name}
            value={option.value}
            checked={value === option.value}
            disabled={disabled}
            onChange={() => onChange(option.value)}
          />
          {option.text}
        </label>
      ))}
    </div>
  );
}
