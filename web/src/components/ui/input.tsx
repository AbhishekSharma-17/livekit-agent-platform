import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Text input (docs/ui/DESIGN-SYSTEM.md section 6.2): at least 36 px, padding
 * 7 px 11 px, `--input` border, 8 px radius, card fill. Hover strengthens the
 * border; focus draws the brand border plus `--focus-shadow` (no outline);
 * disabled is muted with secondary text; placeholders are tertiary.
 *
 * Type size (decision D11): 13.5 px above 640 px; the unlayered phone rule in
 * `globals.css` lifts every text input to 16 px so iOS does not zoom on focus.
 */
export const inputClasses =
  "h-9 w-full min-w-0 rounded border border-input bg-card px-[11px] py-[7px] text-control text-foreground transition-[color,border-color,box-shadow] duration-(--duration-fast) outline-none placeholder:text-text-tertiary hover:border-border-strong focus-visible:border-brand focus-visible:shadow-focus focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-muted disabled:text-text-secondary disabled:hover:border-input aria-invalid:border-destructive-solid file:mr-3 file:inline-flex file:h-6 file:rounded-sm file:border file:border-border file:bg-card file:px-2 file:text-label file:font-medium file:text-foreground"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return <input type={type} data-slot="input" className={cn(inputClasses, className)} {...props} />
}

export { Input }
