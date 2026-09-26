/**
 * Pure edits of the panel layout the composer works on (`config.panel` in the
 * agent editor form). No React, no form — `tests/console-panel-composer.test.tsx`
 * drives these directly.
 *
 * Every function returns a new layout whose blocks are in render order with
 * `order` equal to the position, which is what `buildAgentUpdate` sends.
 */
import type { BlockSpecForm, PanelLayoutForm } from "@/components/console/lib/schemas";
import { panelMeta } from "@/components/shared/panel-meta";
import {
  BLOCK_CATALOG,
  BLOCK_TOOL_TYPES,
  type BlockConfigField,
  type BlockToolName,
} from "@/panels/blocks/catalog";
import { COMPOSITE_PANEL_ID, DEFAULT_COMPOSITE_BLOCKS, type BlockType } from "@/panels/composite/layout";

function renumber(blocks: BlockSpecForm[]): BlockSpecForm[] {
  return blocks.map((block, order) => (block.order === order ? block : { ...block, order }));
}

/** `table`, then `table_2`, `table_3`… — the first id of that stem not taken. */
export function nextBlockId(blocks: readonly BlockSpecForm[], type: BlockType): string {
  const stem = BLOCK_CATALOG[type].idStem;
  const taken = new Set(blocks.map((block) => block.id));
  if (!taken.has(stem)) return stem;
  for (let n = 2; ; n += 1) {
    const candidate = `${stem}_${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Config defaults worth writing for a new block (only non-empty ones). */
export function defaultConfig(type: BlockType): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const field of BLOCK_CATALOG[type].configFields) {
    if (field.kind === "select") config[field.key] = field.default;
  }
  return config;
}

export function addBlock(panel: PanelLayoutForm, type: BlockType): PanelLayoutForm {
  const block: BlockSpecForm = {
    id: nextBlockId(panel.blocks, type),
    type,
    title: null,
    config: defaultConfig(type),
    order: panel.blocks.length,
  };
  return { ...panel, blocks: renumber([...panel.blocks, block]) };
}

export function removeBlock(panel: PanelLayoutForm, index: number): PanelLayoutForm {
  return { ...panel, blocks: renumber(panel.blocks.filter((_, i) => i !== index)) };
}

/** Move the block at `from` to position `to` (clamped). */
export function moveBlock(panel: PanelLayoutForm, from: number, to: number): PanelLayoutForm {
  const target = Math.max(0, Math.min(panel.blocks.length - 1, to));
  if (from === target || from < 0 || from >= panel.blocks.length) return panel;
  const blocks = [...panel.blocks];
  const [moved] = blocks.splice(from, 1);
  blocks.splice(target, 0, moved);
  return { ...panel, blocks: renumber(blocks) };
}

export function updateBlock(panel: PanelLayoutForm, index: number, patch: Partial<BlockSpecForm>): PanelLayoutForm {
  return {
    ...panel,
    blocks: renumber(panel.blocks.map((block, i) => (i === index ? { ...block, ...patch } : block))),
  };
}

/**
 * Set one config key. An empty value (the field's "nothing" — `""`, `false`
 * for an off switch, `[]` for no columns, the default page) is removed, so
 * `BlockSpec.config` only ever carries what the author chose.
 */
export function setBlockConfig(
  panel: PanelLayoutForm,
  index: number,
  field: BlockConfigField,
  value: unknown,
): PanelLayoutForm {
  const block = panel.blocks[index];
  if (!block) return panel;
  const config = { ...block.config };
  const empty =
    value === "" ||
    value === null ||
    value === undefined ||
    (Array.isArray(value) && value.length === 0) ||
    (field.kind === "boolean" && value === false) ||
    (field.kind === "integer" && value === field.default);
  if (empty) delete config[field.key];
  else config[field.key] = value;
  return updateBlock(panel, index, { config });
}

/** Switch panels: blocks survive only on the composite panel. */
export function switchPanel(panel: PanelLayoutForm, panelId: string): PanelLayoutForm {
  if (panelId === panel.panel_id) return panel;
  if (panelId === COMPOSITE_PANEL_ID) {
    const blocks =
      panel.blocks.length > 0
        ? panel.blocks
        : DEFAULT_COMPOSITE_BLOCKS.map((block) => ({
            id: block.id,
            type: block.type,
            title: block.title ?? null,
            config: { ...block.config },
            order: block.order ?? 0,
          }));
    return { panel_id: panelId, layout: panel.panel_id === COMPOSITE_PANEL_ID ? panel.layout : "side", blocks: renumber(blocks) };
  }
  return { panel_id: panelId, layout: panelMeta(panelId).layout, blocks: [] };
}

export interface BlockToolStatus {
  name: BlockToolName;
  /** The panel has a block this tool writes, so the worker registers it. */
  available: boolean;
  enabled: boolean;
  /** Why the switch is off-limits, when it is. */
  reason: string | null;
}

const TOOL_NEEDS: Record<BlockToolName, string> = {
  update_block: "Add a table, document, gallery, sources, transcript, video, details, text, steps or pack block first",
  show_document: "Add a document block first",
  table_append: "Add a table block first",
  request_form: "Add a form block first",
  request_choice: "Add a choices block first",
  resolve_choice: "Add a choices block first",
  set_details: "Add a details block first",
  show_text: "Add a text block first",
  set_steps: "Add a steps block first",
};

/**
 * Whether each block tool would be registered (the worker's
 * `build_builtin_tools` rule, read from `config.panel.blocks`) and whether
 * the author has it switched on (`config.tools.builtin_disabled`).
 *
 * `set_steps` is coarse in `BLOCK_TOOL_TYPES` on purpose (the contract keeps
 * one type set per tool), but the worker registers it only for a `steps`
 * block whose `config.source` is not `"flow"` — a panel with only a
 * flow-driven steps block gets no `set_steps` (ask #43).
 */
export function blockToolStatus(
  panel: PanelLayoutForm,
  builtinDisabled: readonly string[],
): BlockToolStatus[] {
  const blocks = panel.panel_id === COMPOSITE_PANEL_ID ? panel.blocks : [];
  const types = new Set(blocks.map((b) => b.type));
  const hasFlowSteps = blocks.some((b) => b.type === "steps" && b.config?.source === "flow");
  const hasManualSteps = blocks.some((b) => b.type === "steps" && b.config?.source !== "flow");
  return (Object.keys(BLOCK_TOOL_TYPES) as BlockToolName[]).map((name) => {
    const available = name === "set_steps" ? hasManualSteps : [...BLOCK_TOOL_TYPES[name]].some((type) => types.has(type));
    const reason =
      available
        ? null
        : name === "set_steps" && hasFlowSteps
          ? 'This steps block follows the flow; set "Driven by" to "The agent" first'
          : TOOL_NEEDS[name];
    return { name, available, enabled: !builtinDisabled.includes(name), reason };
  });
}
