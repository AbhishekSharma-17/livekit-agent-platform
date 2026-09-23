"use client";

/**
 * `custom` block — pack-defined. The platform has no renderer for a pack's
 * data, so this shows the envelope's `custom` object (and the block's own
 * state, when the pack writes one) as a collapsed JSON view, exactly like
 * the generic panel's "Pack data". A custom React panel that knows the shape
 * renders it itself instead.
 */
import * as React from "react";
import { useMemo } from "react";

import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export function CustomBlock({ spec, data, panel, title, highlighted }: BlockRenderProps) {
  const payload = useMemo(() => {
    const custom = panel.state.custom ?? {};
    const own = data && Object.keys(data).length > 0 ? data : null;
    if (own) return { block: own, custom };
    return Object.keys(custom).length > 0 ? custom : null;
  }, [data, panel.state.custom]);

  const json = useMemo(() => {
    try {
      return JSON.stringify(payload, null, 2);
    } catch {
      return "{}";
    }
  }, [payload]);

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {payload === null ? (
        <PanelEmpty>Nothing from the pack yet.</PanelEmpty>
      ) : (
        <details className="group">
          <summary className="text-muted-foreground hover:text-foreground focus-visible:ring-ring cursor-pointer rounded-sm text-sm focus-visible:ring-2 focus-visible:outline-none">
            Show raw state
          </summary>
          <pre className="bg-muted/40 text-muted-foreground mt-2 max-h-72 overflow-auto rounded-md p-3 font-mono text-[0.8125rem] leading-relaxed">
            {json}
          </pre>
        </details>
      )}
    </BlockFrame>
  );
}
