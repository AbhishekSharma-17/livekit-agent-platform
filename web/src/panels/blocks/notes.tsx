"use client";

/** `notes` block — the envelope's `notes` (WP-9 `NotesBlock`). */
import * as React from "react";

import { NotesBlock as NotesView } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function NotesBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const notes = panel.state.notes ?? [];
  return (
    <BlockFrame spec={spec} title={title} count={notes.length} highlighted={highlighted}>
      <NotesView notes={notes} />
    </BlockFrame>
  );
}
