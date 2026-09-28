"use client";

import * as React from "react";
import { useFormContext, useWatch } from "react-hook-form";

import { useKbs, usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, BlockSpec } from "@/contracts/lkap-contracts";

import { kindOf, type AnyFlowNode } from "./flow-model";
import type { Option, ProviderOption } from "./node-form";
import { nodeToolOptions, toolNodeOptions, type NodeToolOption, type ToolNodeOption } from "./tool-options";

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

/** `details.tsx`: `config.fields` seeds the block's starting rows (`DetailsItem[]`-shaped). Mirrors `use-agent-tool-context.ts`'s own copy (that one reads the query cache; this reads the live form). */
function fieldKeysOf(config: Record<string, unknown> | undefined): string[] {
  const fields = config?.fields;
  if (!Array.isArray(fields)) return [];
  return fields
    .map((field) => (field && typeof field === "object" ? (field as { key?: unknown }).key : undefined))
    .filter((key): key is string => typeof key === "string" && key.length > 0);
}

/**
 * What the node pickers may offer, from the live form (R-V2-10: exactly the
 * agent-level selections) — tools, the agent's knowledge bases, and the
 * available LLM/TTS providers for per-node overrides (R-V2-9); a `tool` node
 * (V6-19, ask #117) additionally gets the agent's attached tool rows (no
 * built-ins) and the panel's `details`/`table` blocks for its bindings —
 * the same shape `useAgentToolContextOptions` gives the tool editors, but
 * from the live form rather than the query cache (the editor's own data).
 */
export function useNodeOptions(agent: AgentOut): {
  toolOptions: NodeToolOption[];
  toolNodeOptions: ToolNodeOption[];
  kbOptions: Option[];
  providerOptions: ProviderOption[];
  detailsBlocks: { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks: { id: string; title: string }[];
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

  const toolStepOptions = React.useMemo(
    () => toolNodeOptions(tools?.tool_ids ?? [], toolRows.data?.items ?? []),
    [tools?.tool_ids, toolRows.data],
  );

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

  const { detailsBlocks, tableBlocks } = React.useMemo(() => {
    const blocks: BlockSpec[] = panel?.blocks ?? [];
    return {
      detailsBlocks: blocks
        .filter((block) => block.type === "details")
        .map((block) => ({ id: block.id, title: block.title ?? block.id, fieldKeys: fieldKeysOf(block.config) })),
      tableBlocks: blocks
        .filter((block) => block.type === "table")
        .map((block) => ({ id: block.id, title: block.title ?? block.id })),
    };
  }, [panel]);

  return { toolOptions, toolNodeOptions: toolStepOptions, kbOptions, providerOptions, detailsBlocks, tableBlocks };
}
