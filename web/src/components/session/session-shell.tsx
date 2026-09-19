"use client";

/**
 * Pure layout for the live session surface. Kept free of LiveKit so the
 * panel-layout seam (`PanelDefinition.layout`, CONTRACTS §11) is testable
 * without a room.
 *
 * - `"side"` (generic panel): the stage owns the main column, the panel docks
 *   to the right.
 * - `"wide"` (e.g. the insurance notebook): the panel owns the main column and
 *   the stage collapses into a left rail.
 *
 * On small screens both collapse to one scrolling column, stage first.
 */
import * as React from "react";

import { cn } from "@/lib/utils";
import type { PanelDefinition } from "@/panels/registry";

export interface SessionShellProps {
  layout: NonNullable<PanelDefinition["layout"]>;
  /** Title shown above the panel column. */
  panelTitle: string;
  banner?: React.ReactNode;
  stage: React.ReactNode;
  transcript: React.ReactNode;
  panel: React.ReactNode;
  controls: React.ReactNode;
}

export function SessionShell({
  layout,
  panelTitle,
  banner,
  stage,
  transcript,
  panel,
  controls,
}: SessionShellProps) {
  const isWide = layout === "wide";

  return (
    <div
      data-testid="session-shell"
      data-layout={layout}
      className="flex h-dvh min-h-0 w-full flex-col"
    >
      {banner}

      <div
        className={cn(
          "grid min-h-0 flex-1 gap-3 p-3 lg:gap-4 lg:p-4",
          isWide
            ? "lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]"
            : "lg:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]",
        )}
      >
        {/* Stage + transcript column */}
        <div
          data-testid="session-stage-column"
          className={cn(
            "flex min-h-0 flex-col gap-3",
            isWide ? "lg:order-1" : "lg:order-1",
          )}
        >
          <div
            className={cn(
              "border-border/60 bg-card/40 flex shrink-0 items-center justify-center overflow-hidden rounded-xl border",
              isWide ? "min-h-40 lg:min-h-56" : "min-h-52 flex-1 lg:min-h-0",
            )}
          >
            {stage}
          </div>
          <div className="border-border/60 bg-card/40 flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border lg:min-h-48">
            {transcript}
          </div>
        </div>

        {/* Panel column */}
        <aside
          data-testid="session-panel-column"
          aria-label={panelTitle}
          className={cn(
            "border-border/60 bg-card/40 flex min-h-0 flex-col overflow-hidden rounded-xl border lg:order-2",
            isWide ? "min-h-[28rem]" : "min-h-80",
          )}
        >
          <header className="border-border/60 flex shrink-0 items-center justify-between border-b px-4 py-2.5">
            <h2 className="text-sm font-semibold tracking-tight">
              {panelTitle}
            </h2>
          </header>
          <div className="min-h-0 flex-1 overflow-hidden">{panel}</div>
        </aside>
      </div>

      <div className="shrink-0 px-3 pb-3 lg:px-4 lg:pb-4">
        <div className="mx-auto w-full max-w-2xl">{controls}</div>
      </div>
    </div>
  );
}
