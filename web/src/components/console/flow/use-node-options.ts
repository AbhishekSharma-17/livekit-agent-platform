"use client";

import * as React from "react";
import { useFormContext, useWatch } from "react-hook-form";

import { useKbs, usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

import type { Option, ProviderOption } from "./node-form";
import { nodeToolOptions, type NodeToolOption } from "./tool-options";

/**
 * What the node pickers may offer, from the live form (R-V2-10: exactly the
 * agent-level selections) — tools, the agent's knowledge bases, and the
 * available LLM/TTS providers for per-node overrides (R-V2-9).
 */
export function useNodeOptions(agent: AgentOut): {
  toolOptions: NodeToolOption[];
  kbOptions: Option[];
  providerOptions: ProviderOption[];
} {
  const { control } = useFormContext<AgentEditorForm>();
  const tools = useWatch({ control, name: "config.tools" });
  const capabilities = useWatch({ control, name: "config.capabilities" });
  const panel = useWatch({ control, name: "config.panel" });
  const knowledge = useWatch({ control, name: "config.knowledge" });
  const toolRows = useTools();
  const packs = usePacks();
  const kbs = useKbs();
  const providers = useProviders();

  const toolOptions = React.useMemo(() => {
    const names: Record<string, string> = {};
    for (const row of toolRows.data?.items ?? []) names[row.id] = row.name;
    const pack = packs.data?.items.find((item) => item.manifest.id === agent.pack_id);
    return nodeToolOptions({
      builtinDisabled: tools?.builtin_disabled ?? [],
      httpRequestEnabled: tools?.http_request_enabled ?? false,
      camera: capabilities?.camera ?? false,
      screenShare: capabilities?.screen_share ?? false,
      blockTypes: (panel?.blocks ?? []).map((block) => block.type),
      packToolNames: pack?.manifest.tool_names ?? [],
      toolIds: tools?.tool_ids ?? [],
      toolNamesById: names,
    });
  }, [agent.pack_id, capabilities, packs.data, panel, toolRows.data, tools]);

  const kbOptions = React.useMemo(() => {
    const byId = new Map((kbs.data?.items ?? []).map((kb) => [kb.id, kb.name]));
    return (knowledge?.kb_ids ?? []).map((id) => ({ value: id, label: byId.get(id) ?? id }));
  }, [kbs.data, knowledge]);

  const providerOptions = React.useMemo(
    () =>
      (providers.data?.providers ?? [])
        .filter(
          (provider) =>
            (provider.kind === "llm" || provider.kind === "tts") &&
            (provider.availability ?? "available") === "available" &&
            provider.enabled !== false,
        )
        .map((provider) => ({
          id: provider.id,
          label: provider.label,
          kind: provider.kind,
          defaultModel: provider.default_model,
        })),
    [providers.data],
  );

  return { toolOptions, kbOptions, providerOptions };
}
