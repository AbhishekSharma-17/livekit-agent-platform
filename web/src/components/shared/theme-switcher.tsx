"use client";

import * as React from "react";
import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { MonitorIcon, MoonIcon, SunIcon, type LucideIcon } from "lucide-react";

import { useThemePreference, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string; icon: LucideIcon }> = [
  { value: "system", label: "System", icon: MonitorIcon },
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
];

export interface ThemeSwitcherProps {
  /** Controlled mode (the styleguide previews a theme without touching the stored preference). */
  value?: ThemePreference;
  onValueChange?: (value: ThemePreference) => void;
  className?: string;
}

/**
 * Theme switcher (docs/ui/DESIGN-SYSTEM.md section 6.7): three icon buttons
 * (System, Light, Dark) in a muted track, a radio group underneath. Placed in
 * the account menu. Uncontrolled, it reads and writes the person's stored
 * preference; until mounted it renders the default so server and client agree.
 */
export function ThemeSwitcher({ value, onValueChange, className }: ThemeSwitcherProps) {
  const preference = useThemePreference();
  const current = value ?? (preference.mounted ? preference.theme : "system");
  const change = (next: string) => {
    const theme = next as ThemePreference;
    if (onValueChange) onValueChange(theme);
    else preference.setTheme(theme);
  };
  return (
    <RadioGroupPrimitive.Root
      data-slot="theme-switcher"
      aria-label="Theme"
      orientation="horizontal"
      value={current}
      onValueChange={change}
      className={cn("inline-flex items-center gap-0.5 rounded bg-muted p-[3px]", className)}
    >
      {OPTIONS.map((option) => (
        <RadioGroupPrimitive.Item
          key={option.value}
          value={option.value}
          aria-label={option.label}
          title={option.label}
          className="inline-flex size-7 items-center justify-center rounded-sm border border-transparent text-text-secondary transition-colors duration-(--duration-fast) hover:text-foreground data-[state=checked]:border-border data-[state=checked]:bg-card data-[state=checked]:text-foreground data-[state=checked]:shadow-raised"
        >
          <option.icon aria-hidden="true" className="size-[15px]" />
        </RadioGroupPrimitive.Item>
      ))}
    </RadioGroupPrimitive.Root>
  );
}
