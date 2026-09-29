"use client";

import * as React from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field } from "@/components/shared/field";
import { useModelCatalog } from "@/components/console/registry/model-combobox";
import {
  EFFORT_LABELS,
  effortToSend,
  lowestEffort,
  offersEffort,
  REASONING_EFFORTS,
  resolveReasoning,
  SLOW_VOICE_EFFORTS,
  type ReasoningEffort,
  type ReasoningView,
} from "@/components/console/registry/reasoning";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

/** The selector's value for "nothing stored" (a Radix select item cannot have an empty value). */
const AUTOMATIC = "__automatic__";

/**
 * The model's reasoning view for an LLM slot (V6-31): the vendor's live list
 * (the same query the model picker makes, so no extra request) and the
 * registry. Unknown for any other kind.
 */
export function useSlotReasoningView(
  spec: ProviderSpec | undefined,
  value: ProviderRef | null | undefined,
  modelId: string,
): ReasoningView {
  const isLlm = spec?.kind === "llm";
  const catalog = useModelCatalog(spec?.id, spec?.catalog, value?.credential_id ?? null, { enabled: isLlm });
  const items = catalog.data?.items;
  return React.useMemo(() => {
    if (!spec || !isLlm) return { reasoning: null, efforts: null, parameters: null };
    const catalogItem = items?.find((item) => item.id === modelId);
    return resolveReasoning({ spec, modelId, catalogItem });
  }, [spec, isLlm, items, modelId]);
}

export interface ReasoningEffortFieldProps {
  id: string;
  view: ReasoningView;
  /** The stored `reasoning_effort` (`""`/absent = automatic). */
  value: string | null | undefined;
  onChange: (next: string | null) => void;
  /** The api's finding on the field, if any. */
  issue?: { message: string; severity: "error" | "warning" } | undefined;
  issuePath?: string;
}

/**
 * "Reasoning effort" (V6-31), shown only for a model that reasons and lists
 * its efforts (or when a value is stored, so it can be cleared). Automatic is
 * what a voice session wants: the lowest level the model offers.
 */
export function ReasoningEffortField({ id, view, value, onChange, issue, issuePath }: ReasoningEffortFieldProps) {
  const stored = value && value.trim() ? value : null;
  const efforts: readonly ReasoningEffort[] = view.efforts && view.efforts.length > 0 ? view.efforts : REASONING_EFFORTS;
  const lowest = lowestEffort(view);
  const sent = effortToSend(view, stored);
  const slow = sent !== null && SLOW_VOICE_EFFORTS.has(sent);
  const automaticLabel = lowest ? `Automatic (lowest: ${EFFORT_LABELS[lowest]})` : "Automatic";

  const note = offersEffort(view)
    ? "Lower is faster. Automatic uses the lowest level the model offers, which suits a live call."
    : "This model does not use a reasoning effort, so this setting is ignored.";
  const warning =
    issue?.severity === "warning" ? issue.message : slow ? "On a live call this adds several seconds to every reply." : null;

  return (
    <Field
      label="Reasoning effort"
      htmlFor={id}
      error={issue?.severity === "error" ? issue.message : undefined}
      hint={
        <>
          {warning ? (
            <span data-slot="field-warning" className="font-medium text-warning-text">
              {warning}{" "}
            </span>
          ) : null}
          {note}
        </>
      }
    >
      <Select value={stored ?? AUTOMATIC} onValueChange={(next) => onChange(next === AUTOMATIC ? null : next)}>
        <SelectTrigger id={id} className="w-full" data-issue-path={issuePath} data-slot="reasoning-effort">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTOMATIC}>{automaticLabel}</SelectItem>
          {efforts.map((effort) => (
            <SelectItem key={effort} value={effort}>
              {EFFORT_LABELS[effort]}
            </SelectItem>
          ))}
          {stored && !(efforts as readonly string[]).includes(stored) ? <SelectItem value={stored}>{stored}</SelectItem> : null}
        </SelectContent>
      </Select>
    </Field>
  );
}
