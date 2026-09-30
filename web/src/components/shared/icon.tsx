import * as React from "react";
import type { LucideIcon, LucideProps } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Icon sizes (docs/ui/DESIGN-SYSTEM.md section 5):
 * - `xs` 12 px: chip remove
 * - `sm` 14 px: small buttons, text links, stat labels
 * - `select` 15 px: selects, segmented controls, back link
 * - `md` 16 px: default
 * - `nav` 17 px: sidebar nav
 * - `tile` 18 px: empty-state tile
 * - `lg` 20 px: phone tab bar
 * - `xl` 24 px: the caller-facing session surface
 */
export type IconSize = "xs" | "sm" | "select" | "md" | "nav" | "tile" | "lg" | "xl";

export const ICON_SIZE_PX: Record<IconSize, number> = { xs: 12, sm: 14, select: 15, md: 16, nav: 17, tile: 18, lg: 20, xl: 24 };

/** Size utilities; they beat the global `.lucide` 16 px default (a base-layer rule). */
export const ICON_SIZE_CLASS: Record<IconSize, string> = {
  xs: "size-3",
  sm: "size-3.5",
  select: "size-[15px]",
  md: "size-4",
  nav: "size-[17px]",
  tile: "size-[18px]",
  lg: "size-5",
  xl: "size-6",
};

export interface IconProps extends Omit<LucideProps, "size" | "ref" | "strokeWidth"> {
  as: LucideIcon;
  size?: IconSize;
  /** Accessible name. Without it the icon is decorative (`aria-hidden`). */
  label?: string;
}

/**
 * Lucide wrapper: token sizes through `size-*` classes, decorative by
 * default. The stroke width comes from the one global `.lucide` rule in
 * `globals.css`; never set it per icon.
 */
export function Icon({ as: Component, size = "md", label, className, ...props }: IconProps) {
  return (
    <Component
      data-slot="icon"
      data-size={size}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
      focusable="false"
      className={cn("shrink-0", ICON_SIZE_CLASS[size], className)}
      {...props}
    />
  );
}
