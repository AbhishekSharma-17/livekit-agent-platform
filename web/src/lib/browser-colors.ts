/**
 * Browser-chrome colours (docs/ui/DESIGN-SYSTEM.md section 2.4).
 *
 * The `theme-color` meta tag, the web manifest and the app icon files cannot
 * read CSS variables, so they mirror three tokens from `src/app/globals.css`
 * as literal hex values: `--background`, `--brand` and `--brand-foreground`,
 * per theme (the icons use the light pair, written by
 * `scripts/gen-app-icons.mjs`). These are the only colour literals allowed
 * outside the token file (the design lint exempts this module), and
 * tests/browser-colors.test.ts fails if they drift from the tokens.
 */
export const BROWSER_COLORS = {
  light: { background: "#f9fafc", brand: "#504cb4", brandForeground: "#ffffff" },
  dark: { background: "#0e1114", brand: "#9fa5f9", brandForeground: "#111223" },
} as const;

/** `viewport.themeColor` for the root layout: the page background per colour scheme. */
export const THEME_COLOR = [
  { media: "(prefers-color-scheme: light)", color: BROWSER_COLORS.light.background },
  { media: "(prefers-color-scheme: dark)", color: BROWSER_COLORS.dark.background },
];
