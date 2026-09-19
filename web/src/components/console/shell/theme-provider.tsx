"use client";

import * as React from "react";

import { ThemeProvider as NextThemesProvider } from "next-themes";

import { DEFAULT_THEME, THEME_STORAGE_KEY } from "@/lib/theme";

/**
 * Console theme provider (docs/UI_UX_SPEC.md §2.2): light by default, user
 * choice Light / Dark / System persisted under `lkap-theme`, applied as a
 * class on `<html>`. Mounted by `app/console/layout.tsx` only — the root
 * layout is untouched and the session surface stays dark via its own
 * `.dark` wrapper. Theme switches never animate.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      storageKey={THEME_STORAGE_KEY}
      defaultTheme={DEFAULT_THEME}
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
