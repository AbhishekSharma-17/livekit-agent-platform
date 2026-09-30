/**
 * The theme provider moved to `components/theme/theme-provider.tsx` (UI-1) so
 * the root layout can mount it without the session bundle reaching console
 * code. This re-export keeps existing imports working.
 */
export { ThemeProvider } from "@/components/theme/theme-provider";
