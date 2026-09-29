"use client";

import * as React from "react";
import { ChevronRightIcon } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  connectionNotes,
  pipelineSummary,
  type ConnectionFacts,
  type PipelineLike,
} from "@/components/console/agents/providers-section/pipeline-summary";
import { useModelCatalog } from "@/components/console/registry/model-combobox";
import { useSlotReasoningView } from "@/components/console/registry/reasoning-effort-field";
import { slotModelId } from "@/components/console/registry/provider-slot-editor";
import { cn } from "@/lib/utils";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * What the language model is called and whether it reasons, read the way the
 * model picker reads it (the vendor's live list, then the provider list), so
 * the summary line and the picker never disagree. Nothing is requested unless
 * the agent uses a separate language model.
 */
function useLlmFacts(pipeline: PipelineLike | null | undefined, providers: readonly ProviderSpec[]) {
  const ref = (pipeline?.mode ?? "cascaded") === "cascaded" ? (pipeline?.llm ?? null) : null;
  const spec = ref?.provider_id ? providers.find((p) => p.id === ref.provider_id) : undefined;
  const providerRef = ref as ProviderRef | null;
  const modelId = spec && providerRef ? slotModelId(spec, providerRef) : "";
  const reasoning = useSlotReasoningView(spec, providerRef, modelId);
  const catalog = useModelCatalog(spec?.id, spec?.catalog, providerRef?.credential_id ?? null, { enabled: Boolean(spec) });
  const label = modelId ? catalog.data?.items?.find((item) => item.id === modelId)?.label : undefined;
  return { reasoning: spec ? reasoning : null, label: label ?? null };
}

export interface PipelineSummaryLineProps {
  pipeline: PipelineLike | null | undefined;
  providers: readonly ProviderSpec[];
  connection?: ConnectionFacts | null;
  /** "section" is the providers section's callout; "rail" is the bare sentence for the summary rail. */
  variant?: "section" | "rail";
  className?: string;
}

/**
 * The one-line pipeline summary (V6-33): "Deepgram Flux listens and decides
 * when you've finished · GPT-6 Luna thinks (reasoning: automatic, lowest) ·
 * Deepgram Aura speaks · runs on DGX (self-hosted)". Renders nothing until at
 * least one part is chosen.
 */
export function PipelineSummaryLine({ pipeline, providers, connection, variant = "section", className }: PipelineSummaryLineProps) {
  const llm = useLlmFacts(pipeline, providers);
  const summary = pipelineSummary({
    pipeline,
    providers,
    connection,
    llmReasoning: llm.reasoning,
    llmModelLabel: llm.label,
  });
  if (summary.text === "") return null;
  if (variant === "rail") {
    return (
      <p data-slot="pipeline-summary" className={cn("text-[0.8125rem] leading-[1.125rem] text-pretty text-foreground", className)}>
        {summary.text}
      </p>
    );
  }
  return (
    <div
      data-slot="pipeline-summary"
      className={cn("flex max-w-[65ch] flex-col gap-1 rounded-lg border border-border bg-muted/40 px-4 py-3", className)}
    >
      <span className="text-xs font-medium text-muted-foreground">How this agent works</span>
      <p className="text-sm leading-5 text-pretty text-foreground">{summary.text}</p>
    </div>
  );
}

/**
 * Where this connection differs from LiveKit Cloud (V6-33): LiveKit Inference,
 * the turn detector, noise cancellation and phone, from the connection's own
 * capabilities. Open by default when the connection lacks something.
 */
export function ConnectionNotes({ connection, className }: { connection: ConnectionFacts | null | undefined; className?: string }) {
  const notes = connectionNotes(connection);
  const limited = notes.filter((note) => note.limited).length;
  if (notes.length === 0) return null;
  return (
    <Collapsible defaultOpen={limited > 0} className={className} data-slot="connection-notes">
      <CollapsibleTrigger className="group/notes inline-flex items-center gap-1 rounded-xs text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ChevronRightIcon className="size-4 transition-transform duration-(--dur-2) group-data-[state=open]/notes:rotate-90" aria-hidden="true" />
        {limited > 0 ? "What LiveKit Cloud has that this connection doesn't" : "What this connection has"}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-2 flex max-w-[65ch] flex-col gap-1.5 text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
          {notes.map((note) => (
            <li key={note.id} data-limited={note.limited ? "true" : undefined}>
              <span className="font-medium text-foreground">{note.label}: </span>
              {note.text}
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
