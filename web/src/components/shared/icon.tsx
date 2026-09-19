import * as React from "react";

import type { LucideIcon, LucideProps } from "lucide-react";

import { cn } from "@/lib/utils";

export type IconSize = "sm" | "md" | "lg" | "xl";

/** §2.6: sm 14 (inline in text), md 16 (buttons, rows), lg 20 (sidebar, section nav), xl 24 (session). */
export const ICON_SIZE_PX: Record<IconSize, number> = { sm: 14, md: 16, lg: 20, xl: 24 };

export interface IconProps extends Omit<LucideProps, "size" | "ref"> {
  as: LucideIcon;
  size?: IconSize;
  /** Accessible name. Without it the icon is decorative (`aria-hidden`). */
  label?: string;
}

/**
 * Lucide wrapper (docs/UI_UX_SPEC.md §2.6): 1.75 absolute stroke, token sizes,
 * decorative by default.
 */
export function Icon({ as: Component, size = "md", label, className, ...props }: IconProps) {
  const px = ICON_SIZE_PX[size];
  return (
    <Component
      width={px}
      height={px}
      strokeWidth={1.75}
      absoluteStrokeWidth
      data-slot="icon"
      data-size={size}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
      focusable="false"
      className={cn("shrink-0", className)}
      {...props}
    />
  );
}
