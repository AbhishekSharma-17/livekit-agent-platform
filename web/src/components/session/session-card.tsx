"use client";

/**
 * The shared card shell for the three full-page session states — pre-call
 * (§5.1), end of call (§5.6) and unavailable (§5.7): a `radius-2xl` card on
 * `background`, centred, safe-area aware.
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
        "bg-card border-border w-full overflow-hidden rounded-2xl border shadow-md",
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
    <div className="flex min-h-[100dvh] flex-col">
      {top}
      <main
        className={cn(
          "flex flex-1 flex-col items-center justify-center gap-4 p-4 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] md:p-6",
          className,
        )}
      >
        {children}
      </main>
    </div>
  );
}
