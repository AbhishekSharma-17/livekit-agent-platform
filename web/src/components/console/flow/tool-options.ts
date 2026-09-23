import { BLOCK_TOOLS, BUILTIN_TOOLS } from "@/components/console/lib/constants";
import { BLOCK_TOOL_TYPES, type BlockToolName } from "@/panels/blocks/catalog";

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

export interface ToolOptionsInput {
  builtinDisabled: readonly string[];
  httpRequestEnabled: boolean;
  camera: boolean;
  screenShare: boolean;
  blockTypes: readonly string[];
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
  const blocks = new Set(input.blockTypes);
  for (const tool of BLOCK_TOOLS) {
    if (disabled.has(tool.name)) continue;
    const types = BLOCK_TOOL_TYPES[tool.name as BlockToolName];
    if (types && [...types].some((type) => blocks.has(type))) {
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
