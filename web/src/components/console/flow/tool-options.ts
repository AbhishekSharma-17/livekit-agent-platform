import { BLOCK_TOOLS, BUILTIN_TOOLS } from "@/components/console/lib/constants";
import { BLOCK_TOOL_TYPES, type BlockToolName } from "@/panels/blocks/catalog";
import type {
  DatasetToolDefinition,
  HttpToolDefinition,
  McpServerDefinition,
  ProviderToolDefinition,
  ToolOut,
} from "@/contracts/lkap-contracts";

/**
 * The tools a flow node may pick (R-V2-10): exactly the agent-level union the
 * api's `lkap_api.flows.validation.allowed_tool_names` accepts, so a picker
 * never offers a name that would fail validation or that the worker would drop.
 *
 * - built-ins the worker registers for this config (not in `builtin_disabled`;
 *   `http_request` only with `http_request_enabled`; the two vision tools only
 *   with camera or screen share),
 * - block tools whose block is in the panel,
 * - the pack's tool names,
 * - the `name` of each tool row selected in `config.tools.tool_ids`.
 */

export interface NodeToolOption {
  name: string;
  label: string;
  group: "Built-in" | "Panel" | "Pack" | "Agent tools";
}

/**
 * The built-ins the worker registers only with camera or screen share
 * (`lkap_contracts.tools.VISION_TOOL_NAMES`; pinned to
 * `contracts/generated/builtin_tools.json` by `tests/tool-names-parity.test.ts`).
 * Which block makes each block tool register is `BLOCK_TOOL_TYPES` in the
 * panel catalog — one web copy, pinned by the same test (V2-19B-3).
 */
export const VISION_TOOL_NAMES: ReadonlySet<string> = new Set(["describe_current_frame", "pin_frame"]);

/** The shape `nodeToolOptions` needs of a panel block: its type and its config. */
export interface ToolOptionBlock {
  type: string;
  config?: Record<string, unknown> | null;
}

export interface ToolOptionsInput {
  builtinDisabled: readonly string[];
  httpRequestEnabled: boolean;
  camera: boolean;
  screenShare: boolean;
  blocks: readonly ToolOptionBlock[];
  packToolNames: readonly string[];
  toolIds: readonly string[];
  /** `{id → name}` of the workspace's tool rows. */
  toolNamesById: Readonly<Record<string, string>>;
}

export function nodeToolOptions(input: ToolOptionsInput): NodeToolOption[] {
  const disabled = new Set(input.builtinDisabled);
  const vision = input.camera || input.screenShare;
  const out: NodeToolOption[] = [];
  for (const tool of BUILTIN_TOOLS) {
    if (disabled.has(tool.name)) continue;
    if (VISION_TOOL_NAMES.has(tool.name) && !vision) continue;
    out.push({ name: tool.name, label: tool.label, group: "Built-in" });
  }
  if (input.httpRequestEnabled && !disabled.has("http_request")) {
    out.push({ name: "http_request", label: "HTTP request", group: "Built-in" });
  }
  const blockTypes = new Set(input.blocks.map((block) => block.type));
  // `set_steps` registers only for a `steps` block that does not follow the
  // flow (`config.source !== "flow"`) — the same rule the composer's block-tool
  // switches apply (`composer-model.ts::blockToolStatus`, ask #43).
  const hasManualSteps = input.blocks.some((block) => block.type === "steps" && block.config?.source !== "flow");
  for (const tool of BLOCK_TOOLS) {
    if (disabled.has(tool.name)) continue;
    if (tool.name === "set_steps") {
      if (hasManualSteps) out.push({ name: tool.name, label: tool.label, group: "Panel" });
      continue;
    }
    const types = BLOCK_TOOL_TYPES[tool.name as BlockToolName];
    if (types && [...types].some((type) => blockTypes.has(type))) {
      out.push({ name: tool.name, label: tool.label, group: "Panel" });
    }
  }
  for (const name of input.packToolNames) out.push({ name, label: name, group: "Pack" });
  for (const id of input.toolIds) {
    const name = input.toolNamesById[id];
    if (name) out.push({ name, label: name, group: "Agent tools" });
  }
  const seen = new Set<string>();
  return out.filter((option) => (seen.has(option.name) ? false : (seen.add(option.name), true)));
}

// ------------------------------------------------------------------ tool node (V6-19, ask #117)

/**
 * One tool the flow's `tool` node may call. Unlike {@link nodeToolOptions} (every tool name the
 * *model* may pick, including built-ins and the pack's own) a tool step calls a row through the
 * same execution path a model call would — the agent's **attached rows only**, no built-ins.
 */
export interface ToolNodeOption {
  /** The tool row's `name` — what `ToolNode.tool` stores. */
  name: string;
  label: string;
  kind: ToolOut["kind"];
  /**
   * For an MCP server row: the sub-tools `ToolNode.mcp_tool` may name — its own
   * `allowed_tools` when set, else its `cached_tools` (ask #117); `null` for every other kind.
   */
  mcpToolNames: string[] | null;
  /** The tool's declared argument names, when known (http/provider: its JSON Schema; dataset: its key columns), for seeding `ToolNode.arguments`. */
  argumentNames: string[];
}

/** Argument names from a JSON Schema's top-level `properties`, minus any pinned ones. */
function schemaArgumentNames(parameters: unknown, pinned: readonly string[]): string[] {
  const properties = (parameters as { properties?: Record<string, unknown> } | null | undefined)?.properties;
  if (!properties) return [];
  return Object.keys(properties).filter((name) => !pinned.includes(name));
}

/** The agent's attached tool rows (`config.tools.tool_ids`), in that order, as {@link ToolNodeOption}s. */
export function toolNodeOptions(toolIds: readonly string[], rows: readonly ToolOut[]): ToolNodeOption[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const out: ToolNodeOption[] = [];
  for (const id of toolIds) {
    const row = byId.get(id);
    if (!row) continue;
    let mcpToolNames: string[] | null = null;
    let argumentNames: string[] = [];
    // `row.kind` (required on `ToolOut`) is the reliable discriminator — the generated
    // union's own `definition.kind` is optional (R-V5-8/ask #20's lesson, `tool-row.tsx`).
    switch (row.kind) {
      case "mcp": {
        const definition = row.definition as McpServerDefinition;
        mcpToolNames = definition.allowed_tools?.length
          ? definition.allowed_tools
          : (definition.cached_tools ?? []).map((tool) => tool.name);
        break;
      }
      case "dataset": {
        const definition = row.definition as DatasetToolDefinition;
        const pinned = Object.keys(definition.pinned_arguments ?? {});
        argumentNames = definition.key_columns.filter((column) => !pinned.includes(column));
        break;
      }
      case "provider": {
        const definition = row.definition as ProviderToolDefinition;
        argumentNames = schemaArgumentNames(definition.parameters, Object.keys(definition.pinned_arguments ?? {}));
        break;
      }
      case "http": {
        const definition = row.definition as HttpToolDefinition;
        argumentNames = schemaArgumentNames(definition.parameters, []);
        break;
      }
    }
    out.push({ name: row.name, label: row.name, kind: row.kind, mcpToolNames, argumentNames });
  }
  return out;
}
