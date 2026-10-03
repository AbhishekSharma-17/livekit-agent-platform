import * as React from "react";

import { cn } from "@/lib/utils";
import { vendorMarkFor } from "./vendor-marks";

export interface VendorMarkProps {
  /** Vendor label, slug or provider id ("Deepgram", "OpenAI", "livekit-inference", "gmail"). */
  vendor: string;
  /**
   * `xs` 16 px with no tile (the compact inline mark before a name in a table
   * cell, a tab, a label or a card title), `sm` 20 px, `md` 24 px (default),
   * `lg` 32 px.
   */
  size?: "xs" | "sm" | "md" | "lg";
  /**
   * Give the mark the vendor's name for assistive tech (and a hover title).
   * Leave it off when the name is printed beside the mark (the usual case),
   * so a screen reader hears the name once; turn it on where marks stand
   * alone, such as a row's stack of provider marks.
   */
  labelled?: boolean;
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
  xs: "size-4 bg-transparent text-[0.5rem] [&>svg]:size-3.5",
  sm: "size-5 text-[0.625rem] [&>svg]:size-3",
  md: "size-6 text-[0.6875rem] [&>svg]:size-3.5",
  lg: "size-8 text-xs [&>svg]:size-[1.125rem]",
} as const;

/**
 * A third-party service's identity (docs/ui/DESIGN-SYSTEM.md section 5).
 *
 * - **The official mark** when `vendor-marks.ts` knows the vendor (Simple
 *   Icons, else Lobe Icons, else the company's own mark), in monochrome
 *   `currentColor` (foreground ink on a muted tile), so it reads in both
 *   themes whatever the brand colour is. For brands that forbid recolouring,
 *   this ink is their approved black (light theme) or white (dark theme)
 *   version (docs/ui/VENDOR-MARKS.md). The one exception is a brand that
 *   offers no one-colour version (Microsoft Outlook, `ink: "colour"`), whose
 *   self-contained icon keeps its own fills on the same tile.
 * - **Otherwise a monogram** in a square faintly tinted with a per-vendor
 *   hue, mixed into theme tokens so it reads in both themes.
 *
 * Always shown with the vendor's name. The mark is decorative by default
 * (`aria-hidden`); pass `labelled` where no visible name sits beside it.
 */
export function VendorMark({ vendor, size = "md", labelled = false, className }: VendorMarkProps) {
  const icon = vendorMarkFor(vendor);
  const a11y = labelled ? { role: "img", "aria-label": vendor, title: vendor } : { "aria-hidden": true as const };
  if (icon) {
    return (
      <span
        {...a11y}
        data-slot="vendor-mark"
        data-mark={icon.slug}
        data-source={icon.source}
        data-ink={icon.ink}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-sm bg-muted-strong text-foreground select-none",
          SIZE_CLASSES[size],
          className,
        )}
      >
        <svg viewBox="0 0 24 24" fill={icon.ink === "colour" ? undefined : "currentColor"} aria-hidden="true" focusable="false">
          {icon.paths.map((path, index) => (
            <path key={index} d={path.d} fillRule={path.fillRule} fill={path.fill} opacity={path.opacity} />
          ))}
        </svg>
      </span>
    );
  }
  const style = { "--vendor-tint": `oklch(0.62 0.1 ${vendorHue(vendor)})` } as React.CSSProperties;
  return (
    <span
      {...a11y}
      data-slot="vendor-mark"
      data-mark="monogram"
      style={style}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-sm font-semibold tracking-tight select-none",
        "bg-[color-mix(in_oklch,var(--vendor-tint)_16%,var(--card))] text-[color-mix(in_oklch,var(--vendor-tint)_30%,var(--foreground))]",
        SIZE_CLASSES[size],
        className,
      )}
    >
      <span aria-hidden="true">{vendorMonogram(vendor)}</span>
    </span>
  );
}

export interface VendorNameProps {
  /** Vendor label, slug, provider id or client id the mark is looked up by. */
  vendor: string;
  /** The printed name. Defaults to `vendor`. */
  children?: React.ReactNode;
  /**
   * Show the tinted monogram when the vendor has no mark. Off by default, since
   * an inline mark is there to help recognise a name, and a two-letter tile
   * beside an unknown name (a script's own client name, "Other") adds noise.
   */
  monogram?: boolean;
  className?: string;
}

/**
 * A printed name with its compact mark before it (`VendorMark` size `xs`),
 * for the places the console names a company or product in a table cell, a
 * tab, a label or a card title. The mark is decorative (`aria-hidden`) and the
 * text stays. Use it beside a name, never in the middle of a sentence.
 */
export function VendorName({ vendor, children, monogram = false, className }: VendorNameProps) {
  const showMark = monogram || vendorMarkFor(vendor) !== null;
  return (
    <span data-slot="vendor-name" className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
      {showMark ? <VendorMark vendor={vendor} size="xs" /> : null}
      <span className="min-w-0">{children ?? vendor}</span>
    </span>
  );
}

export interface ModelNameProps {
  /** A model id as the provider reports it ("openai/gpt-5-mini", "nova-3"). */
  model: string;
  className?: string;
}

/**
 * A model id led by its maker's compact mark when the id names one in its
 * first segment ("openai/gpt-5-mini", "deepgram/nova-3" on a gateway such as
 * LiveKit Inference). A bare id ("nova-3") or a maker with no mark prints the
 * id alone. The maker's name is part of the printed id, so the mark always
 * sits beside it.
 */
export function ModelName({ model, className }: ModelNameProps) {
  const maker = model.includes("/") ? model.split("/")[0] : null;
  if (!maker || vendorMarkFor(maker) === null) return <span className={className}>{model}</span>;
  return (
    <VendorName vendor={maker} className={className}>
      {model}
    </VendorName>
  );
}
