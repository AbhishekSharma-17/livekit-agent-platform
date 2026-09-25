"use client";

import * as React from "react";
import { CheckIcon, ZapIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { RadioGroupItem } from "@/components/ui/radio-group";
import { useProviders } from "@/components/console/lib/api-hooks";
import { formatUsdPerMin } from "@/components/console/lib/cost-hooks";
import { credentialHome } from "@/components/console/registry/provider-meta";
import type { TemplateOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import {
  TEMPLATE_CATEGORY_META,
  TEMPLATE_CHIP_META,
  runsOnInference,
  templateBadges,
  type ProviderLookup,
  type TemplateBadge,
} from "./template-meta";

const BADGE_TONE: Record<TemplateBadge["tone"], string> = {
  warning: "bg-warning-soft text-warning-text",
  info: "bg-info-soft text-info-text",
  neutral: "bg-muted text-muted-foreground",
  success: "bg-transparent px-0 text-muted-foreground",
};

/** A capability chip: icon + label, the one-sentence help as its tooltip. */
export function TemplateChipPill({ chip }: { chip: keyof typeof TEMPLATE_CHIP_META }) {
  const meta = TEMPLATE_CHIP_META[chip];
  return (
    <span
      role="listitem"
      data-slot="template-chip"
      data-chip={chip}
      aria-label={meta.label}
      title={meta.help}
      className="inline-flex h-6 items-center gap-1 rounded-full border border-border bg-background px-2 text-[0.6875rem] leading-none font-medium text-foreground/80"
    >
      <Icon as={meta.icon} size="sm" className="size-3 text-muted-foreground" />
      <span aria-hidden="true">{meta.label}</span>
    </span>
  );
}

export function TemplateBadgePill({ badge }: { badge: TemplateBadge }) {
  return (
    <span
      data-slot="template-badge"
      data-badge={badge.kind}
      title={badge.title}
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-xs px-1.5 text-[0.6875rem] leading-none font-medium tracking-[0.01em] whitespace-nowrap",
        BADGE_TONE[badge.tone],
      )}
    >
      {badge.kind === "keys_present" ? <Icon as={CheckIcon} size="sm" className="size-3 text-success" /> : null}
      {badge.label}
    </span>
  );
}

export interface TemplateTileProps {
  item: TemplateOut;
  selected: boolean;
  /** provider ids the workspace holds a key for (the provider-keys list). */
  keyProviderIds: ReadonlySet<string>;
  providers: ProviderLookup;
  /** Rendered under the tile when selected, below `lg` only (the narrow-screen preview). */
  children?: React.ReactNode;
  className?: string;
}

/**
 * One starter in the gallery (docs/v4/TEMPLATES.md §6.2): a radio styled as
 * a card, with the name, tagline, capability chips, requirement badges and
 * the "Runs on LiveKit Inference" line. The whole card is the radio's label;
 * the radio is named by the starter's name and described by its tagline so a
 * screen reader doesn't read every chip as part of the name.
 */
export function TemplateTile({ item, selected, keyProviderIds, providers, children, className }: TemplateTileProps) {
  const { template } = item;
  const uid = React.useId();
  const category = TEMPLATE_CATEGORY_META[template.category] ?? TEMPLATE_CATEGORY_META.example;
  const chips = template.chips ?? [];
  // `keyProviderIds` comes from the raw credential list, which the api
  // stores under a provider's credential home (R-V4-7: an OpenRouter key
  // added from any of its five slots lands under `openrouter-llm`). A
  // starter that requires an aliased provider id (e.g. `openrouter-stt`)
  // would otherwise never read "Keys present" even though the one shared
  // key covers it, so every alias whose home is already held is folded in
  // here — `templateBadges` itself stays a pure function of ids and never
  // has to know about credential homes. `useProviders()` reuses the
  // registry `CreateAgentDialog` already fetched (same query key), so this
  // costs nothing extra over the network.
  const registry = useProviders().data?.providers;
  const effectiveKeyProviderIds = React.useMemo(() => {
    if (!registry || registry.length === 0) return keyProviderIds;
    const expanded = new Set(keyProviderIds);
    for (const spec of registry) {
      if (keyProviderIds.has(credentialHome(spec))) expanded.add(spec.id);
    }
    return expanded;
  }, [keyProviderIds, registry]);
  const badges = templateBadges(template, effectiveKeyProviderIds, providers);
  const inference = runsOnInference(template);
  const radioId = `template-${template.id.replace(/[^a-z0-9_-]/gi, "-")}`;
  const estimateUsd = item.estimate ? formatUsdPerMin(item.estimate.per_minute_usd_mid) : null;

  return (
    <div
      data-slot="template-tile"
      data-template={template.id}
      data-selected={selected ? "" : undefined}
      className={cn(
        "group/tile relative flex flex-col rounded-lg border bg-card text-card-foreground transition-[border-color,background-color,box-shadow] duration-(--dur-2) ease-out",
        "has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2 has-focus-visible:ring-offset-background",
        selected
          ? "border-brand-line bg-brand-soft/40 shadow-xs ring-1 ring-brand-line"
          : "border-border hover:border-foreground/20 hover:bg-muted/40",
        className,
      )}
    >
      <label htmlFor={radioId} className="flex cursor-pointer flex-col gap-3 p-3.5">
        <span className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className={cn(
              "inline-flex size-9 shrink-0 items-center justify-center rounded-md border transition-colors duration-(--dur-2)",
              selected ? "border-brand-line bg-brand-soft text-brand-text" : "border-border bg-muted/60 text-muted-foreground",
            )}
          >
            <Icon as={category.icon} size="md" />
          </span>
          <span className="min-w-0 flex-1">
            <span id={`${uid}-name`} className="block text-sm leading-5 font-semibold text-foreground">
              {template.name}
            </span>
            <span id={`${uid}-tagline`} className="mt-0.5 block text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
              {template.tagline}
            </span>
          </span>
          <RadioGroupItem
            value={template.id}
            id={radioId}
            aria-labelledby={`${uid}-name`}
            aria-describedby={`${uid}-tagline`}
            className="mt-0.5"
          />
        </span>

        {chips.length > 0 ? (
          <span role="list" aria-label="Capabilities" className="flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <TemplateChipPill key={chip} chip={chip} />
            ))}
          </span>
        ) : null}

        {badges.length > 0 || inference || estimateUsd ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            {badges.map((badge) => (
              <TemplateBadgePill key={badge.kind} badge={badge} />
            ))}
            {estimateUsd ? (
              <span
                data-slot="template-estimate"
                title={`Estimate at list prices as of ${item.estimate?.as_of}, before your own usage`}
                className="inline-flex h-5 items-center rounded-xs bg-muted px-1.5 text-[0.6875rem] leading-none font-medium tracking-[0.01em] whitespace-nowrap text-muted-foreground"
              >
                {estimateUsd} · estimate
              </span>
            ) : null}
            {inference ? (
              <span className="inline-flex items-center gap-1 text-[0.6875rem] leading-4 text-muted-foreground">
                <Icon as={ZapIcon} size="sm" className="size-3" />
                Runs on LiveKit Inference — no vendor key
              </span>
            ) : null}
          </span>
        ) : null}
      </label>
      {selected && children ? (
        <div data-slot="template-tile-preview" className="border-t border-brand-line/60 px-3.5 py-3.5 lg:hidden">
          {children}
        </div>
      ) : null}
    </div>
  );
}
