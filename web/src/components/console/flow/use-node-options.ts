"use client";

import * as React from "react";
import { useFormContext, useWatch } from "react-hook-form";

import { useKbs, usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { kindOf, type AnyFlowNode } from "./flow-model";
import type { Option, ProviderOption } from "./node-form";
import { nodeToolOptions, type NodeToolOption } from "./tool-options";

/**
 * The knowledge a step searches (R-V4-29), as the worker computes it: when no
 * Global or step node lists any knowledge base, every step searches all of
 * the agent's; once one does, a step searches the Global node's picks plus
 * its own, limited to the agent's knowledge bases.
 */
export interface KbScope {
  /** `inherits`: the flow lists none; `scoped`: this step searches `ids`; `none`: it searches nothing. */
  state: "inherits" | "scoped" | "none";
  /** What the step searches, in order. */
  ids: string[];
  /** For a step (not the Global node) of a narrowed flow: the Global node's picks it also searches. */
  fromGlobal: string[];
}

function listedKbIds(node: AnyFlowNode): string[] {
  const kind = kindOf(node);
  if (kind !== "agent" && kind !== "global") return [];
  return "kb_ids" in node && Array.isArray(node.kb_ids) ? node.kb_ids : [];
}

/** Whether any Global or step node lists a knowledge base (the flow narrows knowledge). */
export function flowListsKnowledge(nodes: readonly AnyFlowNode[]): boolean {
  return nodes.some((node) => listedKbIds(node).length > 0);
}

/** `node`'s knowledge scope in the flow `nodes`, or `null` for a node that never searches. */
export function kbScopeFor(
  node: AnyFlowNode,
  nodes: readonly AnyFlowNode[],
  agentKbIds: readonly string[],
): KbScope | null {
  const kind = kindOf(node);
  if (kind !== "agent" && kind !== "global") return null;
  if (!flowListsKnowledge(nodes)) return { state: "inherits", ids: [...new Set(agentKbIds)], fromGlobal: [] };
  const allowed = new Set(agentKbIds);
  const keep = (ids: readonly string[]) => [...new Set(ids)].filter((id) => allowed.has(id));
  const global = nodes.find((item) => kindOf(item) === "global");
  const fromGlobal = kind === "agent" && global ? keep(listedKbIds(global)) : [];
  const ids = keep([...fromGlobal, ...listedKbIds(node)]);
  return { state: ids.length ? "scoped" : "none", ids, fromGlobal };
}

/** The canvas card's tag: `KB: all`, `KB: 2`, `KB: none`; `null` when there is nothing to say. */
export function kbTag(scope: KbScope | null): string | null {
  if (!scope) return null;
  if (scope.state === "inherits") return scope.ids.length ? "KB: all" : null;
  return scope.state === "scoped" ? `KB: ${scope.ids.length}` : "KB: none";
}

/** The agent's attached knowledge bases, live from the editor form (Knowledge section). */
export function useAgentKbIds(): string[] {
  const { control } = useFormContext<AgentEditorForm>();
  const knowledge = useWatch({ control, name: "config.knowledge" });
  return React.useMemo(() => knowledge?.kb_ids ?? [], [knowledge]);
}

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
      blocks: panel?.blocks ?? [],
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
