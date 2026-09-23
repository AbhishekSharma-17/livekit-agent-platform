"use client";

/** `activity` block — the envelope's `activity`, newest first (WP-9 `ActivityBlock`). */
import * as React from "react";

import { ActivityBlock as ActivityView } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function ActivityBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const events = panel.state.activity ?? [];
  return (
    <BlockFrame spec={spec} title={title} count={events.length} highlighted={highlighted}>
      <ActivityView events={events} />
    </BlockFrame>
  );
}
