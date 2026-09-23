import { isInferenceProvider } from "@/components/console/registry/provider-meta";
import type { ConnectionOut, PipelineConfig, ProviderOut, ProviderRef } from "@/contracts/lkap-contracts";

/**
 * Pure helpers behind the providers section's connection-awareness
 * (UI_UX_SPEC-V2-AMENDMENTS §2.3): which connection a slot picker gates
 * against, and the warnings the header's connection-change popover shows
 * before switching one out from under a configured pipeline.
 */

/** The connection a slot editor should gate against: the form's explicit choice, else the workspace default. */
export function resolveBoundConnection(
  connectionId: string | null | undefined,
  connections: ConnectionOut[],
): ConnectionOut | undefined {
  if (connectionId) return connections.find((c) => c.id === connectionId);
  return connections.find((c) => c.is_default) ?? connections[0];
}

function pipelineRefs(pipeline: PipelineConfig): ProviderRef[] {
  return [
    pipeline.stt,
    pipeline.llm,
    pipeline.tts,
    pipeline.realtime,
    pipeline.avatar,
    pipeline.image_gen,
    pipeline.workflow_llm,
    pipeline.vad,
    pipeline.turn_detection,
    pipeline.noise_cancellation,
  ].filter((ref): ref is ProviderRef => Boolean(ref));
}

/**
 * One sentence per configured slot that would stop working on `candidate`
 * (UI_UX_SPEC-V2-AMENDMENTS §2.3: "warns when the new connection lacks
 * installed providers or Inference used by the config").
 */
export function connectionSwitchWarnings(
  pipeline: PipelineConfig,
  providers: ProviderOut[],
  candidate: ConnectionOut,
): string[] {
  const warnings: string[] = [];
  for (const ref of pipelineRefs(pipeline)) {
    const spec = providers.find((p) => p.id === ref.provider_id);
    if (!spec) continue;
    if (isInferenceProvider(spec)) {
      if (!candidate.capabilities?.inference_available) {
        warnings.push(`${spec.label} uses LiveKit Inference, which “${candidate.name}” can't reach.`);
      }
      continue;
    }
    if (!(spec.installed_on ?? []).includes(candidate.id)) {
      warnings.push(`${spec.label} isn't installed on “${candidate.name}”.`);
    }
  }
  return warnings;
}
