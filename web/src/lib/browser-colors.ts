/**
 * Browser-chrome colours (docs/ui/DESIGN-SYSTEM.md section 2.4).
 *
 * The `theme-color` meta tag and the web manifest cannot read CSS variables,
 * so they mirror two tokens from `src/app/globals.css` as literal hex values:
 * `--background` and `--brand`, per theme. These are the only colour literals
 * allowed outside the token file (the design lint exempts this module), and
 * tests/browser-colors.test.ts fails if they drift from the tokens.
 */
export const BROWSER_COLORS = {
  light: { background: "#f9fafc", brand: "#504cb4" },
  dark: { background: "#0e1114", brand: "#9fa5f9" },
} as const;

/** `viewport.themeColor` for the root layout: the page background per colour scheme. */
export const THEME_COLOR = [
  { media: "(prefers-color-scheme: light)", color: BROWSER_COLORS.light.background },
  { media: "(prefers-color-scheme: dark)", color: BROWSER_COLORS.dark.background },
];
