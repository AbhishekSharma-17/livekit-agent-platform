"use client";

/**
 * `notes` block — the envelope's `notes` (WP-9 `NotesBlock`).
 *
 * V6-10 (ask #24, D-V6-19): a note pinned to a block (`Note.block_id`) shows
 * in that block's own margin instead (`<Block>`, `blocks/index.tsx`) — this
 * list omits it, so the same note is not shown twice. A note whose
 * `block_id` names no block on this panel (a stale id, or the block was
 * removed) still shows here, so nothing silently vanishes.
 */
import * as React from "react";

import { NotesBlock as NotesView } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function NotesBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const blocks = panel.state.blocks ?? {};
  const notes = (panel.state.notes ?? []).filter((note) => !note.block_id || !(note.block_id in blocks));
  return (
    <BlockFrame spec={spec} title={title} count={notes.length} highlighted={highlighted}>
      <NotesView notes={notes} />
    </BlockFrame>
  );
}
