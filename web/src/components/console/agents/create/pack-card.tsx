"use client";

import * as React from "react";

import { CAPABILITY_META } from "@/components/console/lib/constants";
import { PANEL_META } from "@/components/shared/panel-meta";
import { Icon, VendorMark } from "@/components/shared";
import { RadioGroupItem } from "@/components/ui/radio-group";
import type { PackManifest } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

const GENERIC_PACK_ID = "generic";

/** Pipeline provider ids in display order (§4.2: "the three/one provider vendors"). */
function pipelineProviderIds(manifest: PackManifest): string[] {
  const { pipeline } = { pipeline: manifest.recommended_pipeline };
  if ((pipeline.mode ?? "cascaded") === "realtime") {
    return pipeline.realtime ? [pipeline.realtime.provider_id] : [];
  }
  return [pipeline.stt, pipeline.llm, pipeline.tts]
    .filter((ref): ref is NonNullable<typeof ref> => Boolean(ref))
    .map((ref) => ref.provider_id);
}

export interface PackCardProps {
  manifest: PackManifest;
  /** provider_id -> vendor label, from `useProviders()`. */
  vendors: Map<string, string>;
  selected: boolean;
  className?: string;
}

/**
 * One pack template card (docs/UI_UX_SPEC.md §4.2): a radio item styled as a
 * card, with a fact list drawn straight from the manifest so the choice is
 * legible before creating anything. The `generic` pack is relabelled "Blank
 * agent" per spec — it has no other special handling.
 */
export function PackCard({ manifest, vendors, selected, className }: PackCardProps) {
  const isBlank = manifest.id === GENERIC_PACK_ID;
  const title = isBlank ? "Blank agent" : manifest.name;
  const description = isBlank
    ? "Cascaded pipeline on LiveKit Inference; block panel; no code tools."
    : manifest.description;
  const providerIds = pipelineProviderIds(manifest);
  // The panel a new agent actually gets: the pack's v2 `default_panel` (the
  // generic pack's is the block panel), else its v1 `ui_panel_id`.
  const panelId = manifest.default_panel?.panel_id ?? manifest.ui_panel_id;
  const panel = PANEL_META[panelId];
  const activeCapabilities = (
    Object.entries(manifest.capabilities ?? {}) as [keyof typeof CAPABILITY_META, boolean | undefined][]
  ).filter(([, on]) => on);
  const toolCount = manifest.tool_names.length;
  const kbCount = manifest.kb_seeds?.length ?? 0;

  return (
    <label
      data-slot="pack-card"
      data-selected={selected ? "" : undefined}
      className={cn(
        "flex cursor-pointer flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground transition-colors",
        "has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2 has-focus-visible:ring-offset-background",
        selected ? "border-brand-line bg-brand-soft/40 ring-1 ring-brand-line" : "hover:bg-muted/50",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <RadioGroupItem value={manifest.id} id={`pack-${manifest.id}`} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">{title}</p>
          <p className="mt-0.5 text-[0.8125rem] text-pretty text-muted-foreground">{description}</p>
        </div>
      </div>

      <dl className="ml-7 grid gap-x-4 gap-y-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div>
          <dt className="font-medium text-foreground">Pipeline</dt>
          <dd className="mt-1 flex items-center gap-1.5">
            <span className="capitalize">{manifest.recommended_pipeline.mode ?? "cascaded"}</span>
            {providerIds.length > 0 ? (
              <span className="flex items-center gap-1">
                {providerIds.map((id) => (
                  <VendorMark key={id} vendor={vendors.get(id) ?? id} size="sm" />
                ))}
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-foreground">Panel</dt>
          <dd className="mt-1">{panel?.label ?? panelId}</dd>
        </div>
        {toolCount > 0 || kbCount > 0 ? (
          <div>
            <dt className="font-medium text-foreground">Tools &amp; knowledge</dt>
            <dd className="mt-1">
              {toolCount > 0 ? `${toolCount} code ${toolCount === 1 ? "tool" : "tools"}` : "No code tools"}
              {kbCount > 0 ? ` · seeds ${kbCount} knowledge ${kbCount === 1 ? "base" : "bases"}` : ""}
            </dd>
          </div>
        ) : null}
        {activeCapabilities.length > 0 ? (
          <div>
            <dt className="font-medium text-foreground">Capabilities</dt>
            <dd className="mt-1 flex items-center gap-2">
              {activeCapabilities.map(([key]) => {
                const meta = CAPABILITY_META[key];
                if (!meta) return null;
                return <Icon key={key} as={meta.icon} size="sm" label={meta.label} />;
              })}
            </dd>
          </div>
        ) : null}
      </dl>
    </label>
  );
}
