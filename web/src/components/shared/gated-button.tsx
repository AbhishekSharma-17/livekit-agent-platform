"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export interface GatedButtonProps extends React.ComponentProps<typeof Button> {
  /** When `false`, the button is disabled and a tooltip explains why. */
  allowed: boolean;
  /** Tooltip copy when `!allowed`; see `components/console/lib/roles.ts::writeAccessReason`. */
  reason?: string;
}

/**
 * A `Button` that disables itself with an explanatory tooltip instead of
 * letting the caller reach a control the api will refuse (docs/v2/_asks.md
 * V2-20-5). Carries its own `TooltipProvider` rather than relying on the one
 * `console-shell.tsx` mounts once for the whole app — Radix providers nest
 * harmlessly, and this keeps the component usable standalone (unit tests
 * that render it outside the console shell; any future non-console caller).
 *
 * A native `disabled` button swallows pointer events, so Radix's tooltip
 * trigger can't sit directly on it — the trigger wraps a non-disabled `span`
 * around the (visually, not functionally) disabled button instead.
 */
export function GatedButton({ allowed, reason, disabled, className, ...props }: GatedButtonProps) {
  if (allowed) return <Button disabled={disabled} className={className} {...props} />;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("inline-flex", className)} tabIndex={0}>
            <Button {...props} disabled aria-disabled="true" className="pointer-events-none w-full" />
          </span>
        </TooltipTrigger>
        <TooltipContent>{reason ?? "You don't have permission to do this."}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
