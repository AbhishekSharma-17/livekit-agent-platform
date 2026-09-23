import type { BlockSpec } from "@/contracts/lkap-contracts";
import type { PanelProps } from "@/panels/registry";


/**
 * What every block component receives from `<Block>`.
 *
 * - `spec`: the `BlockSpec` from the layout (id, type, title, config).
 * - `data`: the block's own state — `UiState.blocks[spec.id]` over the type's
 *   initial state (`catalog.initialBlockState`), so no field is ever missing.
 *   `{}` for the envelope blocks, which read `panel.state` instead.
 * - `panel`: the panel's props (envelope, asset URLs, transcript, `perform`).
 * - `title`: the resolved heading (`spec.title` → the type's default; `null`
 *   renders no heading).
 * - `highlighted`: `show_block` / a `form` request just pointed at this block.
 */
export interface BlockRenderProps<S = Record<string, unknown>> {
  spec: BlockSpec;
  data: S;
  panel: PanelProps;
  title: string | null;
  highlighted?: boolean;
}

/** DOM id of a block's frame, the scroll/focus target for `show_block`. */
export function blockDomId(blockId: string): string {
  return `lkap-block-${blockId}`;
}
