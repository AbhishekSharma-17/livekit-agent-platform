"use client";

import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { useThemePreference, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

/** docs/UI_UX_SPEC.md §4.11: "Appearance: theme radio (Light / Dark / System)". */
export function AppearanceTab() {
  const { theme, options, setTheme, mounted } = useThemePreference();

  return (
    <Section id="appearance" title="Appearance" description="Choose how the console looks on this device.">
      <SectionRow className="flex flex-wrap gap-2" role="radiogroup" aria-label="Theme">
        {options.map((option) => {
          const selected = mounted && theme === option.value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setTheme(option.value as ThemePreference)}
              className={cn(
                "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected
                  ? "border-brand-line bg-brand-soft text-brand-text"
                  : "border-border bg-card text-foreground hover:bg-accent",
              )}
            >
              <Icon as={option.icon} size="md" />
              {option.label}
            </button>
          );
        })}
      </SectionRow>
    </Section>
  );
}
