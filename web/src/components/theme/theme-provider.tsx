"use client";

import * as React from "react";

import { usePathname } from "next/navigation";
import { ThemeProvider as NextThemesProvider } from "next-themes";

import { DEFAULT_THEME, THEME_STORAGE_KEY, forcedThemeForPath } from "@/lib/theme";

/**
 * App-wide theme provider (docs/ui/DESIGN-SYSTEM.md: class-based theming,
 * `.dark` on `<html>`, defaulting to the system setting, no flash on load).
 *
 * Mounted once by the root layout, so the console, `/`, `/login` and the
 * not-found pages all get next-themes' blocking script before first paint.
 * The caller-facing `/s/[slug]` surface is fixed dark (decision D3). It is
 * forced here rather than by a nested provider, because next-themes ignores
 * nested providers. The stored preference is left untouched, so leaving the
 * session page restores the person's own choice. Theme switches never
 * animate.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <NextThemesProvider
      attribute="class"
      storageKey={THEME_STORAGE_KEY}
      defaultTheme={DEFAULT_THEME}
      enableSystem
      disableTransitionOnChange
      forcedTheme={forcedThemeForPath(pathname)}
    >
      {children}
    </NextThemesProvider>
  );
}
