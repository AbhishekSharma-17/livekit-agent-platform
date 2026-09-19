"use client";

import * as React from "react";

import { MonitorIcon, MoonIcon, SunIcon, type LucideIcon } from "lucide-react";
import { useTheme } from "next-themes";

/**
 * Console theme preference (docs/UI_UX_SPEC.md §2.2, §4.11). The provider
 * lives in `components/console/shell/theme-provider.tsx` and is mounted by
 * `app/console/layout.tsx` only; the session surface is dark, fixed.
 */
export type ThemePreference = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "lkap-theme";
export const DEFAULT_THEME: ThemePreference = "light";

export interface ThemeOption {
  value: ThemePreference;
  label: string;
  icon: LucideIcon;
}

/** Light / Dark / System, in menu order (radio items; no sun/moon toggle). */
export const THEME_OPTIONS: readonly ThemeOption[] = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: MonitorIcon },
];

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

export interface ThemePreferenceState {
  /** The stored choice; `DEFAULT_THEME` until mounted. */
  theme: ThemePreference;
  /** What is actually applied ("light" | "dark"); `undefined` until mounted. */
  resolvedTheme: "light" | "dark" | undefined;
  setTheme: (theme: ThemePreference) => void;
  options: readonly ThemeOption[];
  /** The option matching `theme` (label + icon for the menu trigger). */
  current: ThemeOption;
  /** False during SSR and the first client render — gate theme-dependent UI on it. */
  mounted: boolean;
}

/** Typed wrapper over `next-themes`' `useTheme` for the theme menu and Settings. */
export function useThemePreference(): ThemePreferenceState {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  const preference: ThemePreference = mounted && isThemePreference(theme) ? theme : DEFAULT_THEME;
  const resolved =
    mounted && (resolvedTheme === "light" || resolvedTheme === "dark") ? resolvedTheme : undefined;
  const current = THEME_OPTIONS.find((option) => option.value === preference) ?? THEME_OPTIONS[0];

  return {
    theme: preference,
    resolvedTheme: resolved,
    setTheme: (next: ThemePreference) => setTheme(next),
    options: THEME_OPTIONS,
    current,
    mounted,
  };
}
