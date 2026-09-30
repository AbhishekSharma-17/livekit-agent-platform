"use client";

/**
 * The shared card shell for the three full-page session states — pre-call
 * (§5.1), end of call (§5.6) and unavailable (§5.7): a card on `background`,
 * centred, safe-area aware.
 *
 * It is the spec's card (docs/ui/DESIGN-SYSTEM.md section 6.7): a hairline
 * border, the 12 px card radius and **no shadow**. Decision D3 keeps the
 * caller page dark with 16 px body text; the old session-only 20 px radius is
 * gone.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export interface SessionCardProps {
  /** `wide` = the 720 px two-column pre-call card; `narrow` = 480 px. */
  width?: "narrow" | "wide";
  children: React.ReactNode;
  className?: string;
  "data-testid"?: string;
}

export function SessionCard({
  width = "narrow",
  children,
  className,
  ...props
}: SessionCardProps) {
  return (
    <div
      className={cn(
        "bg-card border-border w-full overflow-hidden rounded-lg border",
        width === "wide" ? "max-w-[720px]" : "max-w-[480px]",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function SessionCardScreen({
  top,
  children,
  className,
}: {
  /** Full-bleed chrome above the card (the test-mode bar). */
  top?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      {top}
      <main
        className={cn(
          "flex flex-1 flex-col items-center justify-center gap-4 p-4 pt-[max(1rem,env(safe-area-inset-top,0px))] pb-[calc(1rem+env(safe-area-inset-bottom,0px))] md:p-6",
          className,
        )}
      >
        {children}
      </main>
    </div>
  );
}
