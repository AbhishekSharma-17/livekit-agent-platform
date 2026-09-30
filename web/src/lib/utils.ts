import { createCn } from "cn/config"

/**
 * Class merging (clsx + tailwind-merge semantics) that knows the design
 * system's own scales (docs/ui/TOKENS.md), so `text-label` is a font size and
 * not a colour, and `shadow-raised` / `rounded-dialog` merge with their
 * groups. Import `cn` from here, not from the `cn` package directly: the
 * package's default config would drop `text-label` next to `text-info-text`.
 */
export const cn = createCn({
  extend: {
    theme: {
      text: ["tab", "nav", "caption", "stat-label", "label", "control", "body", "title", "dialog", "page", "stat", "display"],
      shadow: ["raised", "overlay", "modal", "focus"],
      radius: ["dialog", "pill"],
    },
  },
})
