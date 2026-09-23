"use client";

/** `status` block — the envelope's `status` stamp + `progress` (WP-9 `StatusBlock`). */
import * as React from "react";

import { StatusBlock as StatusView } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function StatusBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      <StatusView
        status={panel.state.status}
        progress={panel.state.progress}
        className={title === null ? undefined : "px-0 pt-0"}
      />
    </BlockFrame>
  );
}
