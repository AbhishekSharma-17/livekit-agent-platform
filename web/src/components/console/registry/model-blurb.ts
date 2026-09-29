import { catalogSaysVision } from "@/components/console/registry/model-capabilities";
import { speechStreams } from "@/components/console/registry/provider-meta";
import { isFastForVoice, lowestEffort, resolveReasoning } from "@/components/console/registry/reasoning";
import type { CatalogItem, ModelSpec, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * The one line under a model in a picker that says what the model does
 * (V6-33), from the provider list's capability flags and notes, the vendor's
 * live list and the reasoning view: "Streaming · decides end of turn itself",
 * "Not streaming: waits for the whole sentence", "Reasoning · effort none to
 * high · Can see images". Short, no jargon, no ids. `null` when the platform
 * knows nothing worth saying about this model.
 */
export function modelBlurb({
  provider,
  models,
  modelId,
  catalogItem,
  fields,
}: {
  provider: Pick<ProviderSpec, "kind" | "capabilities"> | undefined;
  /** The provider's suggested models (the reasoning and vision facts it records). */
  models: readonly ModelSpec[];
  modelId: string;
  catalogItem?: CatalogItem | null;
  /** The slot's stored option values (a streaming switch such as `use_realtime` lives there). */
  fields?: Record<string, unknown> | null;
}): string | null {
  if (!provider) return null;
  const caps = provider.capabilities;
  const parts: string[] = [];

  if (provider.kind === "stt" || provider.kind === "tts") {
    const streams = speechStreams(provider, fields);
    if (streams === false) return "Not streaming: waits for the whole sentence";
    if (streams === true) parts.push("Streaming");
    if (provider.kind === "stt" && caps?.end_of_turn === true) parts.push("decides end of turn itself");
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  if (provider.kind === "llm" || provider.kind === "realtime") {
    const suggested = models.find((model) => model.id === modelId);
    const view = resolveReasoning({ spec: { models: [...models] }, modelId, catalogItem });
    if (view.reasoning === true) {
      const efforts = view.efforts ?? [];
      const lowest = lowestEffort(view);
      if (lowest && efforts.length > 1) parts.push(`Reasoning · effort ${lowest} to ${efforts[efforts.length - 1]}`);
      else if (lowest) parts.push(`Reasoning · effort ${lowest}`);
      else parts.push("Reasoning");
    }
    if (isFastForVoice(view, modelId)) parts.push("Fast for voice");
    const sees = suggested?.supports_video === true || catalogSaysVision(catalogItem?.meta) === true;
    if (sees) parts.push(provider.kind === "realtime" ? "Can see video" : "Can see images");
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  return null;
}
