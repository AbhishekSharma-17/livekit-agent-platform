"use client";

import * as React from "react";

import { Icon } from "@/components/shared/icon";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useThemePreference, type ThemePreference } from "@/lib/theme";

/**
 * Sidebar-footer theme control (docs/UI_UX_SPEC.md §3.2): a dropdown with
 * Light / Dark / System as radio items; the trigger shows the current choice
 * as text + icon. No sun/moon toggle (§2.1).
 */
export function ThemeMenu({ className, compact = false }: { className?: string; compact?: boolean }) {
  const { theme, options, setTheme, current, mounted } = useThemePreference();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-sidebar-foreground outline-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
        aria-label="Theme"
      >
        <Icon as={current.icon} size="md" />
        {!compact ? <span className="min-w-0 flex-1 truncate">{mounted ? current.label : "Light"}</span> : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="right" className="w-40">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as ThemePreference)}>
          {options.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              <Icon as={option.icon} size="sm" className="mr-2" />
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
