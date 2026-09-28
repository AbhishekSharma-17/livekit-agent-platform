/**
 * Who may draw on a board, and with what (V6-14, D-V6-16) — a TypeScript port of
 * `lkap_contracts.blocks.canvas_caller_can_draw`. The board itself does not always know
 * which panel it lives in (a notebook's `ink` section renders it via `<Block>`, dropping
 * its own `callerCanDraw` prop — `notebook/sections.tsx`'s `InkSectionView`), so it derives
 * the rule itself from the full panel layout, the same as the worker does from
 * `PanelLayout.blocks`.
 */
import type { BlockSpec } from "@/contracts/lkap-contracts";

export type CanvasToolChoice = "pen" | "highlighter" | "eraser" | "box" | "arrow";

const DRAWABLE_TOOLS: ReadonlySet<string> = new Set<CanvasToolChoice>(["pen", "highlighter", "eraser", "box", "arrow"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Whether the caller may draw on `canvasId`: the board's own `caller_can_draw`, or — for a
 * board a notebook `ink` section claims — that notebook's `caller_can_draw`. A config that
 * does not parse (not an object, or the block missing/not a `canvas`) allows nothing,
 * mirroring the contracts function's "a config that does not validate allows nothing".
 */
export function canvasCallerCanDraw(canvasId: string, blocks: readonly BlockSpec[]): boolean {
  const spec = blocks.find((b) => b.id === canvasId);
  if (!spec || spec.type !== "canvas") return false;
  const ownConfig = isRecord(spec.config) ? spec.config : {};
  if (ownConfig.caller_can_draw === true) return true;
  for (const host of blocks) {
    if (host.type !== "notebook") continue;
    const hostConfig = isRecord(host.config) ? host.config : {};
    const sections = Array.isArray(hostConfig.sections) ? hostConfig.sections : [];
    const claims = sections.some(
      (section) => isRecord(section) && section.kind === "ink" && section.canvas_block_id === canvasId,
    );
    if (claims) return hostConfig.caller_can_draw === true;
  }
  return false;
}

/** `config.tools`, defaulted like `CanvasBlockConfig.tools` and filtered to what the board can actually offer (`text` is reserved, D-V6-16). */
export function canvasTools(config: unknown): readonly CanvasToolChoice[] {
  const record = isRecord(config) ? config : {};
  const raw = Array.isArray(record.tools) ? record.tools : ["pen", "highlighter", "eraser"];
  const seen = new Set<CanvasToolChoice>();
  for (const value of raw) {
    if (typeof value === "string" && DRAWABLE_TOOLS.has(value)) seen.add(value as CanvasToolChoice);
  }
  return [...seen];
}

/** `config.max_strokes`, defaulted like `CanvasBlockConfig.max_strokes` (500), clamped to the contract's cap (2,000). */
export function canvasMaxStrokes(config: unknown): number {
  const record = isRecord(config) ? config : {};
  const value = record.max_strokes;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 1) return 500;
  return Math.min(2000, Math.floor(value));
}

/** `config.signature_mode` (V6-12, reserved for V6-23): a smaller board with a baseline, no shapes. */
export function canvasSignatureMode(config: unknown): boolean {
  const record = isRecord(config) ? config : {};
  return record.signature_mode === true;
}
