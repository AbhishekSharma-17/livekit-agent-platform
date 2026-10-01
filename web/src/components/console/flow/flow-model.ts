import type {
  AgentNode,
  EndNode,
  FlowEdge,
  FlowSpec,
  GlobalNode,
  QaNode,
  ToolNode,
  ToolNodeOutcomes,
  StartNode,
  TransferNode,
  VariableSpec,
} from "@/contracts/lkap-contracts";

/**
 * The flow builder's pure model (V2-16): the `FlowSpec` draft the form holds,
 * node/edge helpers, the client-side structural checks (a mirror of
 * `lkap_contracts.flow.FlowSpec._check_structure` / the api's
 * `draft_flow_issues`, so a dot appears on a node before any save), mapping
 * of server issue paths (`flow.nodes[2].tools[0]`) onto node and edge ids, and
 * `@mention` insertion. No React, no `@xyflow` — importable from tests and from
 * the editor's main chunk.
 */

export type FlowNodeKind = "start" | "agent" | "end" | "global" | "transfer" | "qa" | "tool";
export type AnyFlowNode = StartNode | AgentNode | EndNode | GlobalNode | TransferNode | QaNode | ToolNode;

export interface FlowDraft {
  v: 1;
  nodes: AnyFlowNode[];
  edges: FlowEdge[];
  variables: VariableSpec[];
}

export interface FlowIssue {
  path: string;
  message: string;
  severity: "error" | "warning";
}

/** Node ids become tool names (`go_to_<id>`). */
export const NODE_ID_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;
/** Variables are referenced as `{{ name }}`. */
export const VARIABLE_NAME_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

/**
 * `lkap_contracts.tool_context.ToolContextPlaceholder` (D-V6-22), in the order the console
 * lists them. Defined here (not in `tools/tool-context.ts`, which re-exports it) because a
 * `tool` node's argument templates (D-V6-28) need it too, and `tool-context.ts` already
 * imports `VARIABLE_NAME_PATTERN` from this module — keeping the dependency one-directional
 * avoids a cycle between the two.
 */
export const CTX_PLACEHOLDERS = [
  "session_id",
  "agent_id",
  "caller_phone",
  "caller_identity",
  "language",
  "timezone",
  "channel",
] as const;

export const NODE_KIND_LABEL: Record<FlowNodeKind, string> = {
  start: "Start",
  agent: "Agent step",
  end: "End",
  transfer: "Transfer",
  global: "Global rules",
  qa: "QA scoring",
  tool: "Tool step",
};

/** Kinds that never take an edge (merged into every node / run after the call). */
export const UNCONNECTABLE_KINDS: readonly FlowNodeKind[] = ["global", "qa"];

// ------------------------------------------------------------------- tool node (V6-19, D-V6-28)

/** `lkap_contracts.flow.ToolNodeOutcome`: how a `tool` node's call turned out. */
export type ToolOutcomeKey = "ok" | "error" | "empty";
export const TOOL_OUTCOME_KEYS: readonly ToolOutcomeKey[] = ["ok", "error", "empty"];

/** Plain words for a `tool` node's outcomes (ask #117: "Found" / "Nothing found" / "Failed"). */
export const TOOL_OUTCOME_LABEL: Record<ToolOutcomeKey, string> = {
  ok: "Found",
  empty: "Nothing found",
  error: "Failed",
};

/** The edges leaving `nodeId`, in edge-list order. */
export function outgoingEdges(nodeId: string, edges: readonly FlowEdge[]): FlowEdge[] {
  return edges.filter((edge) => edge.source === nodeId);
}

/** Which outcome (if any) `on` currently assigns `edgeId` to — the first match, `ok`/`error`/`empty` order. */
export function outcomeForEdge(on: ToolNodeOutcomes | undefined, edgeId: string): ToolOutcomeKey | null {
  return TOOL_OUTCOME_KEYS.find((outcome) => (on?.[outcome] ?? "") === edgeId) ?? null;
}

/** `on` with `outcome` pointing at `edgeId` (or unset, for `edgeId: null`) — the inverse of {@link outcomeForEdge}. */
export function setToolOutcome(
  on: ToolNodeOutcomes | undefined,
  outcome: ToolOutcomeKey,
  edgeId: string | null,
): ToolNodeOutcomes {
  return { ...on, [outcome]: edgeId || null };
}

/**
 * Why a `tool` node argument template is not valid — mirrors
 * `lkap_contracts.flow.argument_template_issues` exactly (D-V6-28): a value may name
 * `{{ ctx.<name> }}` (a session value) or `{{ var.<name> }}` (a flow variable), nothing else.
 * Unlike `tool-context.ts`'s `placeholderFieldIssues` (used for a tool's own pinned
 * arguments, url and body template, where a bare `{{ name }}` is one of the tool's own
 * arguments), a flow step has no arguments of its own, so a bare `{{ name }}` is always wrong.
 */
export function toolArgumentIssues(text: string): string[] {
  const issues: string[] = [];
  const anyPlaceholder = /\{\{(.*?)\}\}/g;
  const contextPlaceholder = /^\{\{\s*(?:(ctx|var)\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}$/;
  for (const match of text.matchAll(anyPlaceholder)) {
    const whole = match[0];
    const parsed = contextPlaceholder.exec(whole);
    const namespace = parsed?.[1];
    const name = parsed?.[2];
    if (!parsed || !namespace || !name) {
      const inner = match[1].trim();
      const hint = VARIABLE_NAME_PATTERN.test(inner) ? ` (write {{ var.${inner} }})` : "";
      issues.push(`'${whole}' is not a value this step can fill: use {{ var.<name> }}${hint}`);
      continue;
    }
    if (namespace === "ctx" && !(CTX_PLACEHOLDERS as readonly string[]).includes(name)) {
      issues.push(`'${whole}' is not a session value. Use one of: ${CTX_PLACEHOLDERS.join(", ")}`);
    } else if (namespace === "var" && !VARIABLE_NAME_PATTERN.test(name)) {
      issues.push(`'${whole}' is not a variable name (lowercase letters, digits and _)`);
    }
  }
  return issues;
}

export function kindOf(node: AnyFlowNode): FlowNodeKind {
  return (node.kind ?? "agent") as FlowNodeKind;
}

export function normalizeFlow(flow: FlowSpec | null | undefined): FlowDraft {
  return {
    v: 1,
    nodes: [...((flow?.nodes ?? []) as AnyFlowNode[])],
    edges: [...(flow?.edges ?? [])],
    variables: [...(flow?.variables ?? [])],
  };
}

/** The graph a prompt → flow switch starts from: a start node routed to one step. */
export function seedFlow(): FlowDraft {
  return {
    v: 1,
    nodes: [
      { id: "start", kind: "start", label: "Start", position: [0, 0], greeting: null, greeting_mode: "say" },
      {
        id: "main",
        kind: "agent",
        label: "Main step",
        position: [0, 160],
        instructions: "Help the caller with their request.",
      },
    ],
    edges: [{ id: "e_start_main", source: "start", target: "main", condition: "The call has started." }],
    variables: [],
  };
}

/** A fresh, pattern-safe id derived from `label` (`Collect details` → `collect_details`, then `_2`, `_3`…). */
export function uniqueNodeId(label: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  let base = label
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!/^[a-z]/.test(base)) base = `step${base ? `_${base}` : ""}`;
  base = base.slice(0, 28).replace(/_+$/, "");
  if (!used.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}_${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

export function uniqueEdgeId(source: string, target: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const base = `e_${source}_${target}`;
  if (!used.has(base)) return base;
  for (let n = 2; ; n += 1) {
    if (!used.has(`${base}_${n}`)) return `${base}_${n}`;
  }
}

/** A new node of `kind` with sensible defaults. */
export function newNode(kind: FlowNodeKind, existingIds: Iterable<string>, position: [number, number]): AnyFlowNode {
  const label = NODE_KIND_LABEL[kind];
  const id = uniqueNodeId(kind === "agent" ? "step" : kind, existingIds);
  switch (kind) {
    case "start":
      return { id, kind, label, position, greeting: null, greeting_mode: "say" };
    case "agent":
      return { id, kind, label: "New step", position, instructions: "" };
    case "end":
      return { id, kind, label, position, farewell: null, disposition: null, webhook_event: true };
    case "transfer":
      return { id, kind, label, position, to: "", mode: "cold", announce: null };
    case "global":
      return { id, kind, label, position, instructions: "" };
    case "qa":
      return { id, kind, label, position, rubric_prompt: null };
    case "tool":
      return { id, kind, label, position, tool: "", arguments: {}, bindings: [], on: { ok: "" } };
  }
}

export function canHaveOutgoing(kind: FlowNodeKind): boolean {
  return kind === "start" || kind === "agent" || kind === "tool";
}

export function canHaveIncoming(kind: FlowNodeKind): boolean {
  return kind === "agent" || kind === "end" || kind === "transfer" || kind === "tool";
}

/** Remove a node and every edge touching it. */
export function removeNode(flow: FlowDraft, nodeId: string): FlowDraft {
  return {
    ...flow,
    nodes: flow.nodes.filter((node) => node.id !== nodeId),
    edges: flow.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
  };
}

export function updateNode(flow: FlowDraft, nodeId: string, next: AnyFlowNode): FlowDraft {
  return { ...flow, nodes: flow.nodes.map((node) => (node.id === nodeId ? next : node)) };
}

export function updateEdge(flow: FlowDraft, edgeId: string, next: FlowEdge): FlowDraft {
  return { ...flow, edges: flow.edges.map((edge) => (edge.id === edgeId ? next : edge)) };
}

/** Rename a variable everywhere a node extracts it (placeholders in text are left to the author). */
export function renameVariable(flow: FlowDraft, from: string, to: string): FlowDraft {
  return {
    ...flow,
    variables: flow.variables.map((variable) => (variable.name === from ? { ...variable, name: to } : variable)),
    nodes: flow.nodes.map((node) =>
      kindOf(node) === "agent" && (node as AgentNode).extract?.includes(from)
        ? { ...node, extract: (node as AgentNode).extract?.map((name) => (name === from ? to : name)) }
        : node,
    ),
  };
}

// ------------------------------------------------------------------ validation

function reachableFrom(startId: string, edges: readonly FlowEdge[]): Set<string> {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  const seen = new Set([startId]);
  const queue = [startId];
  while (queue.length > 0) {
    for (const target of outgoing.get(queue.pop() as string) ?? []) {
      if (!seen.has(target)) {
        seen.add(target);
        queue.push(target);
      }
    }
  }
  return seen;
}

/**
 * Structural checks of a draft, each at an addressable path — the same rules
 * the api enforces (`draft_flow_issues`), so the canvas shows them instantly.
 */
export function flowDraftIssues(flow: FlowDraft): FlowIssue[] {
  const issues: FlowIssue[] = [];
  const error = (path: string, message: string) => issues.push({ path, message, severity: "error" });
  const warning = (path: string, message: string) => issues.push({ path, message, severity: "warning" });
  if (flow.nodes.length === 0 && flow.edges.length === 0) return issues;

  const kinds = new Map<string, FlowNodeKind>();
  flow.nodes.forEach((node, i) => {
    if (!NODE_ID_PATTERN.test(node.id)) {
      error(`flow.nodes[${i}].id`, "Ids use lowercase letters, digits and _ and start with a letter.");
    }
    if (kinds.has(node.id)) error(`flow.nodes[${i}].id`, `Two nodes can't share the id "${node.id}".`);
    else kinds.set(node.id, kindOf(node));
    if (kindOf(node) === "transfer" && !(node as TransferNode).to?.trim()) {
      error(`flow.nodes[${i}].to`, "Add the number or address to transfer to.");
    }
    if (kindOf(node) === "agent" && !(node as AgentNode).instructions?.trim()) {
      warning(`flow.nodes[${i}].instructions`, "This step has no instructions.");
    }
    if (kindOf(node) === "tool") {
      const tool = node as ToolNode;
      if (!tool.tool?.trim()) error(`flow.nodes[${i}].tool`, "Pick a tool for this step to call.");
      const leaving = new Set(outgoingEdges(tool.id, flow.edges).map((edge) => edge.id));
      if (!tool.on?.ok || !leaving.has(tool.on.ok)) {
        error(`flow.nodes[${i}].on`, "Pick the path taken when the tool succeeds (Found).");
      }
      for (const outcome of TOOL_OUTCOME_KEYS) {
        const edgeId = tool.on?.[outcome];
        if (edgeId && !leaving.has(edgeId)) {
          error(`flow.nodes[${i}].on`, `"${TOOL_OUTCOME_LABEL[outcome]}" names a path that doesn't leave this step.`);
        }
      }
      Object.entries(tool.arguments ?? {}).forEach(([name, value]) => {
        if (typeof value !== "string") return;
        const argIssues = toolArgumentIssues(value);
        if (argIssues.length > 0) error(`flow.nodes[${i}].arguments.${name}`, argIssues[0]);
      });
    }
  });

  const starts = flow.nodes.flatMap((node, i) => (kindOf(node) === "start" ? [i] : []));
  if (starts.length === 0) error("flow.nodes", "A flow needs a start node.");
  starts.slice(1).forEach((i) => error(`flow.nodes[${i}]`, "A flow has exactly one start node."));
  flow.nodes
    .flatMap((node, i) => (kindOf(node) === "global" ? [i] : []))
    .slice(1)
    .forEach((i) => error(`flow.nodes[${i}]`, "A flow has at most one global node."));

  const edgeIds = new Set<string>();
  flow.edges.forEach((edge, j) => {
    if (edgeIds.has(edge.id)) error(`flow.edges[${j}].id`, `Two paths can't share the id "${edge.id}".`);
    edgeIds.add(edge.id);
    for (const role of ["source", "target"] as const) {
      const kind = kinds.get(edge[role]);
      if (kind === undefined) error(`flow.edges[${j}].${role}`, `Unknown node "${edge[role]}".`);
      else if (UNCONNECTABLE_KINDS.includes(kind)) {
        error(`flow.edges[${j}].${role}`, `A ${NODE_KIND_LABEL[kind].toLowerCase()} node can't be connected.`);
      }
    }
    const sourceKind = kinds.get(edge.source);
    if (sourceKind === "end" || sourceKind === "transfer") {
      error(`flow.edges[${j}]`, `An ${NODE_KIND_LABEL[sourceKind].toLowerCase()} node has no outgoing paths.`);
    }
    if (sourceKind === "tool") {
      // D-V6-28: a tool step's outgoing edges are chosen by outcome (`on.ok`/`.error`/`.empty`)
      // in the node's own inspector, not by a free-text condition here — mirrors
      // `_check_tool_nodes`'s "every edge leaving the node is named by an outcome".
      const source = flow.nodes.find((candidate) => candidate.id === edge.source) as ToolNode | undefined;
      if (source && outcomeForEdge(source.on, edge.id) === null) {
        error(`flow.edges[${j}]`, "Assign this path to an outcome (Found, Nothing found or Failed) in the tool step.");
      }
    } else if (!edge.condition?.trim()) {
      warning(`flow.edges[${j}].condition`, "Describe when to take this path. The model reads it.");
    }
  });

  const variableNames = new Set<string>();
  flow.variables.forEach((variable, k) => {
    if (!VARIABLE_NAME_PATTERN.test(variable.name)) {
      error(`flow.variables[${k}].name`, "Names use lowercase letters, digits and _ and start with a letter.");
    }
    if (variableNames.has(variable.name)) error(`flow.variables[${k}].name`, `"${variable.name}" is declared twice.`);
    variableNames.add(variable.name);
  });
  flow.nodes.forEach((node, i) => {
    if (kindOf(node) !== "agent") return;
    ((node as AgentNode).extract ?? []).forEach((name, j) => {
      if (!variableNames.has(name)) error(`flow.nodes[${i}].extract[${j}]`, `"${name}" is not a flow variable.`);
    });
  });

  if (starts.length === 1) {
    const reachable = reachableFrom(flow.nodes[starts[0]].id, flow.edges);
    flow.nodes.forEach((node, i) => {
      const kind = kindOf(node);
      if (kind !== "start" && !UNCONNECTABLE_KINDS.includes(kind) && !reachable.has(node.id)) {
        error(`flow.nodes[${i}]`, "Not reachable from the start node. Connect it.");
      }
    });
  }
  return issues;
}

export function hasErrors(issues: readonly FlowIssue[]): boolean {
  return issues.some((issue) => issue.severity === "error");
}

// ------------------------------------------------------------- issue targeting

export interface IssueTarget {
  kind: "node" | "edge" | "variable" | "flow";
  /** Node or edge id (variables: the variable name). */
  id: string | null;
  /** The field below the node/edge (`tools`, `providers.llm`, …), if any. */
  field: string | null;
}

/**
 * Which node/edge a flow issue belongs to. Accepts server paths in both
 * shapes the console sees (`flow.nodes[2].tools[0]` from the api,
 * `flow.nodes.2.tools.0` after the editor normalises them) and indexes into
 * the flow the issue was computed for.
 */
export function issueTarget(path: string | null | undefined, flow: FlowDraft): IssueTarget | null {
  if (!path) return null;
  const dotted = path.replace(/\[(\d+)\]/g, ".$1").replace(/^config\./, "");
  if (!dotted.startsWith("flow")) return null;
  const parts = dotted.split(".");
  const collection = parts[1];
  const index = parts[2] !== undefined && /^\d+$/.test(parts[2]) ? Number(parts[2]) : null;
  const field = parts.length > 3 ? parts[3] + (parts[4] && !/^\d+$/.test(parts[4]) ? `.${parts[4]}` : "") : null;
  if (collection === "nodes" && index !== null) {
    return { kind: "node", id: flow.nodes[index]?.id ?? null, field };
  }
  if (collection === "edges" && index !== null) {
    return { kind: "edge", id: flow.edges[index]?.id ?? null, field };
  }
  if (collection === "variables" && index !== null) {
    return { kind: "variable", id: flow.variables[index]?.name ?? null, field };
  }
  return { kind: "flow", id: null, field: null };
}

export interface TargetIssues {
  errors: FlowIssue[];
  warnings: FlowIssue[];
}

/** Issues grouped per node id and per edge id (for the dots), plus flow-level ones. */
export function groupIssues(issues: readonly FlowIssue[], flow: FlowDraft) {
  const nodes = new Map<string, TargetIssues>();
  const edges = new Map<string, TargetIssues>();
  const general: FlowIssue[] = [];
  for (const issue of issues) {
    const target = issueTarget(issue.path, flow);
    const bucket =
      target?.kind === "node" && target.id ? nodes : target?.kind === "edge" && target.id ? edges : null;
    if (!bucket || !target?.id) {
      general.push(issue);
      continue;
    }
    const entry = bucket.get(target.id) ?? { errors: [], warnings: [] };
    (issue.severity === "error" ? entry.errors : entry.warnings).push(issue);
    bucket.set(target.id, entry);
  }
  return { nodes, edges, general };
}

/** Field → first error and first warning for one node or edge, for the inspector form. */
export function fieldMessages(
  entry: TargetIssues | undefined,
  flow: FlowDraft,
): { errors: Record<string, string>; warnings: Record<string, string> } {
  const pick = (issues: readonly FlowIssue[]) => {
    const out: Record<string, string> = {};
    for (const issue of issues) {
      const field = issueTarget(issue.path, flow)?.field?.split(".")[0];
      if (field && !out[field]) out[field] = issue.message;
    }
    return out;
  };
  return { errors: pick(entry?.errors ?? []), warnings: pick(entry?.warnings ?? []) };
}

// ------------------------------------------------------------------- mentions

/** The `@query` being typed right before `caret`, if any. */
export function mentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const match = /(^|[^\w@{])@([a-z0-9_]*)$/i.exec(text.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2].toLowerCase() };
}

/** Replace the `@query` ending at `caret` with `{{ name }}`; returns the new text and caret. */
export function insertMention(text: string, caret: number, name: string): { text: string; caret: number } {
  const found = mentionQuery(text, caret);
  const start = found ? found.start : caret;
  const token = `{{ ${name} }}`;
  const next = `${text.slice(0, start)}${token}${text.slice(caret)}`;
  return { text: next, caret: start + token.length };
}
