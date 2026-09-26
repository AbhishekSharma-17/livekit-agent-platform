"use client";

/**
 * The persistent "you're talking to an AI assistant" banner (V5-15's
 * `consent` block, `ConsentBlockConfig.show_banner`; V5-17).
 *
 * This is deliberately panel-level, not block-level: a banner tied to one
 * consent block's own position in the layout (`panels/blocks/consent.tsx`)
 * would scroll away the moment that block left the viewport, but the whole
 * point of this banner is that it stays visible for the entire session
 * (PLAN-V5 V5-17's acceptance: "always visible while `show_banner`"). So
 * `CompositePanel` renders it once, sticky at the top of its own scroll
 * container — it pushes the blocks below it down and never overlays the
 * transcript, unlike a fixed overlay would (`docs/v5/_asks.md`: wired into
 * `composite/index.tsx` outside this package's exclusive files, ratified
 * the way `DEFAULT_RECORDING` was for V5-15).
 *
 * "No other source" (ask #103/#105): only a `consent` block with
 * `show_banner` produces this banner — an agent with `disclosure.position
 * == "banner"` and no consent block in its panel shows nothing here, by
 * design (ask #105, left open, not this card's to close).
 */
import { InfoIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { BlockSpec, ConsentBlockState } from "@/contracts/lkap-contracts";

/** The plain wording shown before any state (or config) text is known. */
const FALLBACK_BANNER_TEXT = "You're talking to an AI assistant.";

function isBannerBlock(spec: BlockSpec): boolean {
  if (spec.type !== "consent") return false;
  const config = spec.config as { show_banner?: unknown } | null | undefined;
  // `ConsentBlockConfig.show_banner` defaults to `true`; only an explicit `false` opts out.
  return config?.show_banner !== false;
}

/**
 * The wording to show: the block's live state text (the worker fills an
 * empty config text from the workspace's wording, so the state is always
 * the exact text the caller was shown), else the block's config text, else
 * the plain fallback.
 */
function textOf(spec: BlockSpec, blocks: Record<string, unknown> | undefined): string {
  const state = blocks?.[spec.id] as ConsentBlockState | undefined;
  const stateText = typeof state?.text === "string" ? state.text.trim() : "";
  if (stateText) return stateText;
  const configText = (spec.config as { text?: unknown } | null | undefined)?.text;
  if (typeof configText === "string" && configText.trim()) return configText.trim();
  return FALLBACK_BANNER_TEXT;
}

/**
 * The banner text for a layout, or `null` when nothing in it opts in. Only
 * the first matching consent block wins — one banner, not a stack, even
 * with more than one consent block in the layout.
 */
export function bannerTextOf(blocks: readonly BlockSpec[], state: Record<string, unknown> | undefined): string | null {
  const spec = blocks.find(isBannerBlock);
  return spec ? textOf(spec, state) : null;
}

/** The banner strip itself: sticky, low-key, an icon and one line. */
export function ConsentBanner({ text }: { text: string }) {
  return (
    <div
      data-slot="consent-banner"
      role="note"
      className="border-border bg-muted/60 text-muted-foreground sticky top-0 z-10 flex items-center gap-2 border-b px-4 py-2 text-[0.8125rem]"
    >
      <Icon as={InfoIcon} size="sm" className="shrink-0" />
      <span className="min-w-0">{text}</span>
    </div>
  );
}
