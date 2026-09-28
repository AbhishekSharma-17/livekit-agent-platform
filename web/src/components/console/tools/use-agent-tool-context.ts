"use client";

import { useQuery } from "@tanstack/react-query";

import type { AgentOut, BlockSpec } from "@/contracts/lkap-contracts";

export interface AgentToolContextOptions {
  /** `null` on the shared `/console/tools` page (no agent attached yet) or while loading. */
  loaded: boolean;
  variableNames: string[];
  detailsBlocks: { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks: { id: string; title: string }[];
}

const EMPTY: AgentToolContextOptions = { loaded: false, variableNames: [], detailsBlocks: [], tableBlocks: [] };

/**
 * What the tool editors' "Insert value" picker and Bindings target picker offer from the
 * attached agent (V6-11): its flow's variables (until V6-15 adds extraction fields, the only
 * source), and its panel's `details`/`table` blocks (D-V6-23 — the only block types a binding
 * may write). `agentId: null` (a tool with no agent yet, or the shared tools list) degrades to
 * free text everywhere the picker would otherwise suggest a name.
 *
 * Reads the agent from the query cache only (`enabled: false` — never fetches on its own):
 * the tool editor is a small dialog opened from an agent's own Tools tab, which has already
 * loaded that agent's full config for its own form, or from the shared `/console/tools` list,
 * which hasn't. A cache hit is a bonus, not a requirement — issuing a fetch of its own here
 * would add a network call a plain "edit a field and save" flow neither expects nor needs, and
 * would race the console's own save request in a test asserting the *first* request made.
 */
export function useAgentToolContextOptions(agentId: string | null): AgentToolContextOptions {
  const agentQuery = useQuery<AgentOut>({
    queryKey: ["agents", agentId ?? ""],
    queryFn: () => Promise.reject(new Error("not fetched here")),
    enabled: false,
  });
  const agent = agentQuery.data;
  if (!agentId || !agent?.config) return EMPTY;

  const variableNames = (agent.config.flow?.variables ?? []).map((v) => v.name);
  const blocks: BlockSpec[] = agent.config.panel?.blocks ?? [];

  const detailsBlocks = blocks
    .filter((block) => block.type === "details")
    .map((block) => ({
      id: block.id,
      title: block.title ?? block.id,
      fieldKeys: fieldKeysOf(block.config),
    }));
  const tableBlocks = blocks
    .filter((block) => block.type === "table")
    .map((block) => ({ id: block.id, title: block.title ?? block.id }));

  return { loaded: true, variableNames, detailsBlocks, tableBlocks };
}

/** `details.tsx`: `config.fields` seeds the block's starting rows (`DetailsItem[]`-shaped). */
function fieldKeysOf(config: Record<string, unknown> | undefined): string[] {
  const fields = config?.fields;
  if (!Array.isArray(fields)) return [];
  return fields
    .map((field) => (field && typeof field === "object" ? (field as { key?: unknown }).key : undefined))
    .filter((key): key is string => typeof key === "string" && key.length > 0);
}
