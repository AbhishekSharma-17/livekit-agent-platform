"use client";

import * as React from "react";
import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string = string> {
  value: T;
  label: string;
  /** Optional count beside the label ("Failed 3"). */
  count?: number;
  icon?: LucideIcon;
}

export interface SegmentedControlProps<T extends string = string> {
  /** Accessible name, e.g. "Filter by status". */
  label: string;
  value: T;
  onValueChange: (value: T) => void;
  options: SegmentedOption<T>[];
  className?: string;
}

/**
 * Segmented control (docs/ui/DESIGN-SYSTEM.md section 6.7) for 2–8 peer
 * **filters or modes**: a `--muted` track with 3 px padding and 28 px items;
 * the selected item gets the card fill, a hairline and the raised shadow.
 * A radio group underneath (arrow keys move and select). It scrolls
 * sideways on phones. For sections of one page use `Tabs`.
 *
 * A count keeps its own words in the accessible name ("Live (1)"), so it
 * never runs into the label ("Live1"); the visible figure is decorative.
 */
export function SegmentedControl<T extends string = string>({
  label,
  value,
  onValueChange,
  options,
  className,
}: SegmentedControlProps<T>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="segmented-control"
      aria-label={label}
      orientation="horizontal"
      value={value}
      onValueChange={(next) => onValueChange(next as T)}
      className={cn(
        "inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded bg-muted p-[3px] [scrollbar-width:none]",
        className,
      )}
    >
      {options.map((option) => (
        <RadioGroupPrimitive.Item
          key={option.value}
          value={option.value}
          data-slot="segmented-item"
          aria-label={option.count !== undefined ? `${option.label} (${option.count})` : undefined}
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-sm border border-transparent px-2.5 text-control font-medium whitespace-nowrap text-text-secondary transition-colors duration-(--duration-fast) hover:text-foreground data-[state=checked]:border-border data-[state=checked]:bg-card data-[state=checked]:text-foreground data-[state=checked]:shadow-raised"
        >
          {option.icon ? <option.icon aria-hidden="true" className="size-[15px]" /> : null}
          {option.label}
          {option.count !== undefined ? (
            <span aria-hidden="true" className="text-caption text-text-tertiary tabular-nums">
              {option.count}
            </span>
          ) : null}
        </RadioGroupPrimitive.Item>
      ))}
    </RadioGroupPrimitive.Root>
  );
}
