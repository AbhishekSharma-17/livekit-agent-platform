"use client";

/**
 * The composer's live preview: the author's draft layout rendered by the
 * composite panel against the block fixtures — the same `CompositePreview`
 * the preview route's `scene=composite` screenshots — on the dark session
 * surface, at the session's panel width (400 px side / wider for `wide`).
 */
import * as React from "react";

import type { PanelLayoutForm } from "@/components/console/lib/schemas";
import { CompositePreview } from "@/components/preview/block-scenes";
import { cn } from "@/lib/utils";

export function ComposerPreview({ panel }: { panel: PanelLayoutForm }) {
  const wide = panel.layout === "wide";
  return (
    <div
      data-testid="composer-preview"
      data-surface="session"
      className="dark bg-stage text-foreground flex justify-center rounded-lg p-3"
    >
      <div
        className={cn(
          "border-border bg-card max-h-[40rem] w-full overflow-hidden rounded-xl border",
          wide ? "max-w-2xl" : "max-w-[400px]",
        )}
      >
        <CompositePreview layout={panel} className="max-h-[40rem] overflow-y-auto" />
      </div>
    </div>
  );
}

export default ComposerPreview;
