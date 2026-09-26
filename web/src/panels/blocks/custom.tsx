"use client";

/**
 * `custom` block — pack-defined. The platform has no renderer for a pack's
 * data, so this shows the envelope's `custom` object (and the block's own
 * state, when the pack writes one) as a collapsed JSON view, exactly like
 * the generic panel's "Pack data". A custom React panel that knows the shape
 * renders it itself instead.
 *
 * One exception (D-V5-33): a `custom` block with `config.kind ==
 * "flow_progress"` keeps the flow runtime's raw `FlowState` mirror on the
 * wire (`agent/src/lkap_agent/flow/runtime.py::publish_progress`), but is
 * rendered with the `steps` timeline, not the JSON dump — `stepsFromFlowProgress`
 * (`./steps`) adapts the mirror into a `StepsBlockState`.
 */
import * as React from "react";
import { useMemo } from "react";

import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import { StepsBlock, stepsFromFlowProgress } from "./steps";
import type { BlockRenderProps } from "./types";

const FLOW_PROGRESS_KIND = "flow_progress";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function CustomBlock({ spec, data, panel, title, highlighted }: BlockRenderProps) {
  const isFlowProgress = isRecord(spec.config) && spec.config.kind === FLOW_PROGRESS_KIND;
  const flowSteps = useMemo(() => stepsFromFlowProgress(data), [data]);

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

  if (isFlowProgress) {
    return <StepsBlock spec={spec} data={flowSteps} panel={panel} title={title} highlighted={highlighted} />;
  }

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
