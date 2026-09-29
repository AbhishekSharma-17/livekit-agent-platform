import type { CatalogItem, ModelCapabilities, ProviderModelOut, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * Reasoning models in the console (V6-31): the console's reading of the
 * capability view the api resolves for the worker
 * (`lkap_api.custom_models.capabilities`, `lkap_contracts.providers.reasoning_effort_to_send`).
 * OpenRouter's catalog record is the one vendor list that says per model
 * whether it reasons, which efforts it takes and which request parameters its
 * endpoints accept; the registry adds what it knows for the models it lists.
 */

export type ReasoningEffort = NonNullable<ModelCapabilities["reasoning_efforts"]>[number];

/** `lkap_contracts.providers.REASONING_EFFORTS`, lowest first. */
export const REASONING_EFFORTS: readonly ReasoningEffort[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

/** The registry field that sets it. */
export const REASONING_EFFORT_FIELD = "reasoning_effort";

/** Efforts that add seconds to every reply on a voice call (`SLOW_VOICE_EFFORTS`). */
export const SLOW_VOICE_EFFORTS: ReadonlySet<string> = new Set(["medium", "high", "xhigh", "max"]);

/** Plain words for each effort in the selector. */
export const EFFORT_LABELS: Record<ReasoningEffort, string> = {
  none: "None (no thinking)",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
};

/** What the console knows about a model's reasoning; `null` = unknown. */
export interface ReasoningView {
  reasoning: boolean | null;
  efforts: ReasoningEffort[] | null;
  parameters: string[] | null;
}

const UNKNOWN: ReasoningView = { reasoning: null, efforts: null, parameters: null };

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : null;
}

function ordered(values: readonly string[]): ReasoningEffort[] {
  return REASONING_EFFORTS.filter((effort) => values.includes(effort));
}

/** An OpenRouter catalog record's reasoning (mirrors the api's `_openrouter_reasoning`). */
export function catalogReasoning(meta: Record<string, unknown> | undefined | null): ReasoningView {
  const parameters = strings(meta?.supported_parameters);
  if (!meta || parameters === null) return UNKNOWN;
  const record = asRecord(meta.reasoning);
  const pricing = asRecord(meta.pricing);
  const reasoning = parameters.includes("reasoning") || record !== undefined || (pricing !== undefined && "internal_reasoning" in pricing);
  let efforts: ReasoningEffort[] | null = [];
  if (reasoning && parameters.includes(REASONING_EFFORT_FIELD)) {
    const listed = strings(record?.supported_efforts);
    efforts = listed && listed.length > 0 ? ordered(listed) : null;
  }
  return { reasoning, efforts, parameters };
}

/** What the registry records for a model it lists. */
export function registryReasoning(spec: Pick<ProviderSpec, "models">, modelId: string): ReasoningView {
  const listed = (spec.models ?? []).find((model) => model.id === modelId);
  if (!listed || listed.reasoning == null) return UNKNOWN;
  return { reasoning: listed.reasoning, efforts: ordered(listed.reasoning_efforts ?? []), parameters: null };
}

function fromCapabilities(caps: ModelCapabilities | null | undefined): ReasoningView {
  if (!caps) return UNKNOWN;
  return {
    reasoning: typeof caps.reasoning === "boolean" ? caps.reasoning : null,
    efforts: caps.reasoning_efforts ? ordered(caps.reasoning_efforts) : null,
    parameters: caps.request_parameters ?? null,
  };
}

/**
 * Declared → detected → the vendor's live list → the registry → unknown,
 * per field (the api's `resolve_capabilities` order).
 */
export function resolveReasoning({
  spec,
  modelId,
  catalogItem,
  record,
}: {
  spec: Pick<ProviderSpec, "models">;
  modelId: string;
  catalogItem?: CatalogItem | null;
  record?: ProviderModelOut | null;
}): ReasoningView {
  const layers = [fromCapabilities(record?.declared), fromCapabilities(record?.detected), catalogReasoning(catalogItem?.meta), registryReasoning(spec, modelId)];
  return {
    reasoning: layers.find((layer) => layer.reasoning !== null)?.reasoning ?? null,
    efforts: layers.find((layer) => layer.efforts !== null)?.efforts ?? null,
    parameters: layers.find((layer) => layer.parameters !== null)?.parameters ?? null,
  };
}

/** Whether the model accepts a request parameter; `null` = unknown. */
export function acceptsParameter(view: ReasoningView, name: string): boolean | null {
  return view.parameters === null ? null : view.parameters.includes(name);
}

/** Whether the effort selector belongs on this model: it reasons and lists its efforts. */
export function offersEffort(view: ReasoningView): boolean {
  return view.reasoning === true && (view.efforts?.length ?? 0) > 0 && acceptsParameter(view, REASONING_EFFORT_FIELD) !== false;
}

/** The lowest effort the model lists, or `null`. */
export function lowestEffort(view: ReasoningView): ReasoningEffort | null {
  return view.efforts && view.efforts.length > 0 ? view.efforts[0] : null;
}

/** The effort a request carries for a configured value (mirrors `reasoning_effort_to_send`). */
export function effortToSend(view: ReasoningView, requested: string | null | undefined): string | null {
  const wanted = requested && requested.trim() ? requested : null;
  if (view.reasoning === null && view.efforts === null) return wanted;
  if (view.reasoning === false || acceptsParameter(view, REASONING_EFFORT_FIELD) === false) return null;
  const efforts = view.efforts;
  if (wanted !== null) {
    if (!efforts || efforts.length === 0 || (efforts as string[]).includes(wanted)) return wanted;
    const rank = (REASONING_EFFORTS as readonly string[]).indexOf(wanted);
    if (rank < 0) return null;
    const above = efforts.filter((effort) => REASONING_EFFORTS.indexOf(effort) > rank);
    return above[0] ?? efforts[efforts.length - 1];
  }
  return efforts && efforts.length > 0 ? efforts[0] : null;
}

/**
 * Model ids small enough to answer quickly (a dated, curated note, not a
 * measurement of every model): on 2026-09-29 GPT-4.1 mini answered a spoken
 * turn in 3.6 s end to end and GPT-6 Luna at its default effort in 8.2 s.
 */
export const FAST_VOICE_NOTE = {
  asOf: "2026-09-29",
  smallModel: /(^|[-/._])(mini|nano|flash|flash-lite|lite|haiku|small|luna)([-/._:]|$)/i,
  text: "Answers quickly on a live call: a small model that thinks little or not at all before it speaks.",
} as const;

/**
 * "Fast for voice": a small model (FAST_VOICE_NOTE) that either does not
 * reason or can reason at `none`/`minimal`, which is what a voice session
 * sends when no effort is set.
 */
export function isFastForVoice(view: ReasoningView, modelId: string): boolean {
  if (!FAST_VOICE_NOTE.smallModel.test(modelId)) return false;
  if (view.reasoning === false) return true;
  const lowest = lowestEffort(view);
  return view.reasoning === true && (lowest === "none" || lowest === "minimal");
}
