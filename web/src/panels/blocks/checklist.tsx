"use client";

/** `checklist` block — the envelope's `checklist` (WP-9 `ChecklistBlock`). */
import * as React from "react";

import { ChecklistBlock as ChecklistView } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function ChecklistBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const items = panel.state.checklist ?? [];
  return (
    <BlockFrame spec={spec} title={title} count={items.length} highlighted={highlighted}>
      <ChecklistView items={items} />
    </BlockFrame>
  );
}
