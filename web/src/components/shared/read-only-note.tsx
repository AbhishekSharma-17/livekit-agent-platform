import * as React from "react";
import { LockIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface ReadOnlyNoteProps {
  /** Plain words with a next step, e.g. "Ask an admin to add connections." */
  children?: React.ReactNode;
  /** `inline` sits in a page header's action slot; `block` fills a card or empty state. */
  variant?: "inline" | "block";
  className?: string;
}

/**
 * Read-only note (docs/ui/DESIGN-SYSTEM.md section 8.5, decision D12): what a
 * person sees **instead of** a page-level primary action they can't use.
 * Never a disabled button with a tooltip, never a lone padlock: the icon
 * always comes with a sentence that names the next step ("Ask an admin").
 * Row and edit actions are hidden outright (see `IfCan`).
 */
export function ReadOnlyNote({ children, variant = "inline", className }: ReadOnlyNoteProps) {
  const text = children ?? "You can view this. Ask an admin to make changes.";
  return (
    <p
      data-slot="read-only-note"
      data-variant={variant}
      className={cn(
        "flex items-start gap-1.5 text-label text-text-secondary",
        variant === "inline" ? "max-w-[40ch]" : "rounded border border-border bg-muted px-3 py-2.5",
        className,
      )}
    >
      <LockIcon aria-hidden="true" className="mt-0.5 size-3.5 text-text-tertiary" />
      <span>{text}</span>
    </p>
  );
}
