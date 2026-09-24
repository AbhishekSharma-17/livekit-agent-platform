"use client";

import * as React from "react";
import { ChevronRightIcon, CircleDashedIcon, MessageCircleIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { VendorMark } from "@/components/shared/vendor-mark";
import { CAPABILITY_META } from "@/components/console/lib/constants";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { TemplateOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import {
  PIPELINE_MODE_LABEL,
  TEMPLATE_CATEGORY_META,
  TEMPLATE_CHIP_META,
  effectiveCapabilities,
  effectivePipeline,
  flowFact,
  instructionsExcerpt,
  knowledgeFact,
  panelFact,
  pipelineProviderIds,
  toolsFact,
  type ProviderLookup,
} from "./template-meta";

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-baseline gap-3 py-2 first:pt-0 last:pb-0">
      <dt className="text-xs font-medium text-muted-foreground">{term}</dt>
      <dd className="min-w-0 text-[0.8125rem] leading-[1.125rem] text-foreground">{children}</dd>
    </div>
  );
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return <p className="text-[0.6875rem] font-semibold tracking-[0.06em] text-muted-foreground uppercase">{children}</p>;
}

/** The last path segment of a model id (`deepgram/nova-3` → `nova-3`). */
function modelName(model: string | null | undefined): string | null {
  if (!model) return null;
  return model.split("/").pop() ?? model;
}

export interface TemplatePreviewProps {
  item: TemplateOut;
  providers: ProviderLookup;
  /**
   * `pane`: the right-hand pane at `lg+` (header, description, everything).
   * `inline`: under the selected tile on narrow screens (the tile already
   * shows the name and chips, so no header).
   */
  variant?: "pane" | "inline";
  className?: string;
}

/**
 * What the selected starter creates (docs/v4/TEMPLATES.md §6.2): the
 * description, a fact list (pipeline, panel, tools, knowledge, flow, call
 * scoring, capabilities), "Try saying" prompts, the post-create next steps
 * and a collapsed look at the instructions. Every fact is read from the
 * template, falling back to its pack (`template.pipeline ?? pack.recommended_pipeline`, …).
 */
export function TemplatePreview({ item, providers, variant = "pane", className }: TemplatePreviewProps) {
  const { template } = item;
  const pipeline = effectivePipeline(item);
  const mode = pipeline.mode ?? "cascaded";
  const providerIds = pipelineProviderIds(pipeline);
  const vendors = [...new Set(providerIds.map((id) => providers.get(id)?.vendor ?? id))];
  const models = (mode === "cascaded" ? [pipeline.stt, pipeline.llm, pipeline.tts] : [pipeline.realtime, pipeline.tts])
    .map((ref) => modelName(ref?.model))
    .filter((name): name is string => Boolean(name));
  const tools = toolsFact(item);
  const knowledge = knowledgeFact(item);
  const flow = flowFact(template);
  const qaOn = Boolean(template.qa?.enabled) || (template.flow?.nodes ?? []).some((node) => node.kind === "qa");
  const capabilities = effectiveCapabilities(item);
  const capabilityIcons = (["camera", "screen_share"] as const).filter((key) => capabilities[key]);
  const keypad = Boolean(capabilities.dtmf);
  const prompts = template.sample_prompts ?? [];
  const steps = template.next_steps ?? [];
  const instructions = instructionsExcerpt(item);
  const category = TEMPLATE_CATEGORY_META[template.category] ?? TEMPLATE_CATEGORY_META.example;
  const headingId = React.useId();

  return (
    <section
      aria-labelledby={variant === "pane" ? headingId : undefined}
      aria-label={variant === "inline" ? `About ${template.name}` : undefined}
      data-slot="template-preview"
      data-variant={variant}
      className={cn("flex flex-col gap-5", className)}
    >
      {variant === "pane" ? (
        <header className="flex flex-col gap-2">
          <span className="inline-flex items-center gap-1.5 text-[0.6875rem] font-semibold tracking-[0.06em] text-brand-text uppercase">
            <Icon as={category.icon} size="sm" className="size-3.5" />
            {category.label}
          </span>
          <h3 id={headingId} className="text-lg leading-6 font-semibold tracking-[-0.01em] text-foreground">
            {template.name}
          </h3>
          <p className="text-[0.8125rem] leading-5 text-pretty text-muted-foreground">{template.description}</p>
        </header>
      ) : (
        <p className="text-[0.8125rem] leading-5 text-pretty text-muted-foreground">{template.description}</p>
      )}

      <div className="flex flex-col gap-2">
        <SubHeading>What you get</SubHeading>
        <dl className="divide-y divide-border rounded-md border border-border bg-card px-3 py-2.5">
          <Fact term="Pipeline">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{PIPELINE_MODE_LABEL[mode]}</span>
              {vendors.length > 0 ? (
                <span className="flex items-center gap-1">
                  {vendors.map((vendor) => (
                    <VendorMark key={vendor} vendor={vendor} size="sm" />
                  ))}
                </span>
              ) : null}
            </span>
            {models.length > 0 ? (
              <span className="mt-0.5 block truncate font-mono text-[0.6875rem] text-muted-foreground" title={models.join(" · ")}>
                {models.join(" · ")}
              </span>
            ) : null}
          </Fact>
          <Fact term="Panel">{panelFact(item)}</Fact>
          {tools ? <Fact term="Tools">{tools}</Fact> : null}
          {knowledge ? <Fact term="Knowledge">{knowledge}</Fact> : null}
          {flow ? <Fact term="Flow">{flow}</Fact> : null}
          {qaOn ? <Fact term="Call scoring">Scores every call</Fact> : null}
          {capabilityIcons.length > 0 || keypad ? (
            <Fact term="Caller can">
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {capabilityIcons.map((key) => (
                  <span key={key} className="inline-flex items-center gap-1">
                    <Icon as={CAPABILITY_META[key].icon} size="sm" className="text-muted-foreground" />
                    {CAPABILITY_META[key].label}
                  </span>
                ))}
                {keypad ? (
                  <span className="inline-flex items-center gap-1">
                    <Icon as={TEMPLATE_CHIP_META.dtmf.icon} size="sm" className="text-muted-foreground" />
                    Keypad
                  </span>
                ) : null}
              </span>
            </Fact>
          ) : null}
        </dl>
      </div>

      {prompts.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHeading>Try saying</SubHeading>
          <ul className="flex flex-col gap-1.5">
            {prompts.map((prompt) => (
              <li
                key={prompt}
                className="flex items-start gap-2 rounded-md bg-muted/60 px-2.5 py-1.5 text-[0.8125rem] leading-[1.125rem] text-foreground"
              >
                <Icon as={MessageCircleIcon} size="sm" className="mt-0.5 text-muted-foreground" />
                <span className="min-w-0 text-pretty">“{prompt}”</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {steps.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHeading>After creating</SubHeading>
          <ol className="flex flex-col gap-1.5">
            {steps.map((step) => (
              <li key={step.label} className="flex items-start gap-2 text-[0.8125rem] leading-[1.125rem] text-foreground">
                <Icon as={CircleDashedIcon} size="sm" className="mt-0.5 text-muted-foreground" />
                <span className="min-w-0 text-pretty">{step.label}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {instructions && variant === "pane" ? (
        <Collapsible className="group/instructions rounded-md border border-border">
          <CollapsibleTrigger className="flex w-full items-center gap-1.5 rounded-md px-3 py-2 text-left text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            <Icon
              as={ChevronRightIcon}
              size="sm"
              className="transition-transform duration-(--dur-2) group-data-[state=open]/instructions:rotate-90"
            />
            Instructions
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p
              tabIndex={0}
              className="max-h-56 overflow-y-auto border-t border-border px-3 py-2.5 text-xs leading-5 whitespace-pre-wrap text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
            >
              {instructions}
            </p>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </section>
  );
}
