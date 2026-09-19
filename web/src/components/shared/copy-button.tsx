"use client";

import * as React from "react";

import { CheckIcon, CopyIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { Icon } from "./icon";

export interface CopyButtonProps {
  /** Text written to the clipboard. */
  value: string;
  /** Accessible name, e.g. "Copy public link". */
  label: string;
  /** Icon button size: `xs` 24 px, `sm` 28 px (default), `md` 32 px. */
  size?: "xs" | "sm" | "md";
  className?: string;
}

const SIZE_TO_BUTTON = { xs: "icon-xs", sm: "icon-sm", md: "icon" } as const;

/** How long the check glyph stays after a successful copy. */
export const COPY_FEEDBACK_MS = 1200;

/**
 * Copy-to-clipboard icon button (docs/UI_UX_SPEC.md §2.7): swaps the icon to a
 * check for 1.2 s and announces the result through a polite live region.
 */
export function CopyButton({ value, label, size = "sm", className }: CopyButtonProps) {
  const [status, setStatus] = React.useState<"idle" | "copied" | "failed">("idle");
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus("idle"), COPY_FEEDBACK_MS);
  };

  const copied = status === "copied";

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size={SIZE_TO_BUTTON[size]}
        aria-label={label}
        data-slot="copy-button"
        data-copied={copied ? "" : undefined}
        onClick={onClick}
        className={cn("text-muted-foreground hover:text-foreground", copied && "text-success-text", className)}
      >
        <Icon as={copied ? CheckIcon : CopyIcon} size={size === "md" ? "md" : "sm"} />
      </Button>
      <span role="status" aria-live="polite" className="sr-only">
        {status === "copied" ? "Copied" : status === "failed" ? "Couldn't copy" : ""}
      </span>
    </>
  );
}
