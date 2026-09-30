"use client";

import { Section, SectionRow } from "@/components/shared/section";
import { ThemeSwitcher } from "@/components/shared/theme-switcher";

/**
 * Appearance: the shared theme switcher (docs/ui/DESIGN-SYSTEM.md section
 * 6.7), the same control as the account menu's, so both read and write the
 * one stored preference. It applies at once, so this card has no Save.
 */
export function AppearanceTab() {
  return (
    <Section id="appearance" title="Appearance" description="Choose how the console looks on this device.">
      <SectionRow className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="text-control font-medium text-foreground">Theme</p>
          <p className="text-caption text-text-secondary">System follows your device&apos;s light or dark setting.</p>
        </div>
        <ThemeSwitcher />
      </SectionRow>
    </Section>
  );
}
