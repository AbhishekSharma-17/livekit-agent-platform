import * as React from "react";

import { cn } from "@/lib/utils";

export interface VendorMarkProps {
  /** Vendor label or id ("Deepgram", "OpenAI", "livekit-inference"). */
  vendor: string;
  /** `sm` 20 px, `md` 24 px (default), `lg` 32 px. */
  size?: "sm" | "md" | "lg";
  className?: string;
}

/**
 * Two-letter monogram: first letters of the first two words ("LiveKit
 * Inference" / "livekit-inference" → "LI"); a single word is split on
 * camelCase ("ElevenLabs" → "EL", "OpenAI" → "OA"), else "Xy" ("deepgram" → "De").
 */
export function vendorMonogram(vendor: string): string {
  let words = vendor.split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (words.length === 1) {
    words = words[0].replace(/([a-z0-9])([A-Z])/g, "$1 $2").split(" ");
  }
  if (words.length === 0) return "?";
  if (words.length === 1) {
    const word = words[0];
    return word.charAt(0).toUpperCase() + word.slice(1, 2).toLowerCase();
  }
  return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
}

/** Stable hue (0–359) for a vendor name, so each vendor keeps its tint. */
export function vendorHue(vendor: string): number {
  let hash = 0;
  const key = vendor.trim().toLowerCase();
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return hash % 360;
}

const SIZE_CLASSES = {
  sm: "size-5 text-[0.625rem]",
  md: "size-6 text-[0.6875rem]",
  lg: "size-8 text-xs",
} as const;

/**
 * Vendor identity without logos (docs/UI_UX_SPEC.md §2.7, §8): a monogram in
 * a square faintly tinted with a per-vendor hue, mixed into theme tokens so it
 * reads in both themes.
 */
export function VendorMark({ vendor, size = "md", className }: VendorMarkProps) {
  const style = { "--vendor-tint": `oklch(0.62 0.1 ${vendorHue(vendor)})` } as React.CSSProperties;
  return (
    <span
      role="img"
      aria-label={vendor}
      title={vendor}
      data-slot="vendor-mark"
      style={style}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-xs font-semibold tracking-tight select-none",
        "bg-[color-mix(in_oklch,var(--vendor-tint)_16%,var(--card))] text-[color-mix(in_oklch,var(--vendor-tint)_30%,var(--foreground))]",
        SIZE_CLASSES[size],
        className,
      )}
    >
      <span aria-hidden="true">{vendorMonogram(vendor)}</span>
    </span>
  );
}
