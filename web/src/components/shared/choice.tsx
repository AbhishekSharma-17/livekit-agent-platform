import * as React from "react";

import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

/**
 * Choice controls (docs/ui/DESIGN-SYSTEM.md section 6.2).
 *
 * - `CheckboxRow` / `RadioRow`: native 16 px controls (`accent-color` comes
 *   from `globals.css`) in a label row, nudged 2 px down to align with the text.
 * - `OptionCard`: a bordered, clickable label around a native radio or
 *   checkbox with a bold title and a small description. Checked cards get the
 *   brand border and brand-subtle fill in pure CSS (`:has(input:checked)`).
 *   Use them instead of bare radios when the choice needs explaining.
 * - `SwitchRow`: a bold label and small description on the left, the switch
 *   on the right.
 */
type NativeInputProps = Omit<React.ComponentProps<"input">, "type" | "children">;

interface ChoiceRowProps extends NativeInputProps {
  label: React.ReactNode;
  description?: React.ReactNode;
}

function ChoiceRow({ type, label, description, className, id, ...props }: ChoiceRowProps & { type: "checkbox" | "radio" }) {
  const autoId = React.useId();
  const inputId = id ?? autoId;
  const descriptionId = description ? `${inputId}-description` : undefined;
  return (
    <div data-slot={`${type}-row`} className={cn("flex items-start gap-2.5", className)}>
      <input
        id={inputId}
        type={type}
        aria-describedby={descriptionId}
        className="mt-0.5 size-4 shrink-0 cursor-pointer disabled:cursor-not-allowed"
        {...props}
      />
      <div className="flex min-w-0 flex-col gap-0.5">
        <label htmlFor={inputId} className="cursor-pointer text-control leading-5 text-foreground">
          {label}
        </label>
        {description ? (
          <p id={descriptionId} className="text-caption text-text-secondary">
            {description}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function CheckboxRow(props: ChoiceRowProps) {
  return <ChoiceRow type="checkbox" {...props} />;
}

export function RadioRow(props: ChoiceRowProps) {
  return <ChoiceRow type="radio" {...props} />;
}

export interface OptionCardProps extends Omit<NativeInputProps, "title"> {
  /** `radio` (default) for one of many, `checkbox` for independent options. */
  type?: "radio" | "checkbox";
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Optional trailing content (a badge, a price). */
  aside?: React.ReactNode;
}

export function OptionCard({ type = "radio", title, description, aside, className, id, ...props }: OptionCardProps) {
  const autoId = React.useId();
  const inputId = id ?? autoId;
  const descriptionId = description ? `${inputId}-description` : undefined;
  return (
    <label
      htmlFor={inputId}
      data-slot="option-card"
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 transition-colors duration-(--duration-fast) hover:bg-muted has-checked:border-brand-border has-checked:bg-brand-subtle has-disabled:cursor-not-allowed has-disabled:opacity-50 has-focus-visible:border-brand has-focus-visible:shadow-focus",
        className,
      )}
    >
      <input
        id={inputId}
        type={type}
        aria-describedby={descriptionId}
        className="mt-0.5 size-4 shrink-0 focus-visible:outline-none"
        {...props}
      />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-control leading-5 font-semibold text-foreground">{title}</span>
        {description ? (
          <span id={descriptionId} className="text-caption text-text-secondary">
            {description}
          </span>
        ) : null}
      </span>
      {aside ? <span className="shrink-0">{aside}</span> : null}
    </label>
  );
}

export interface SwitchRowProps extends Omit<React.ComponentProps<typeof Switch>, "children"> {
  label: React.ReactNode;
  description?: React.ReactNode;
}

export function SwitchRow({ label, description, className, id, ...props }: SwitchRowProps) {
  const autoId = React.useId();
  const switchId = id ?? autoId;
  const descriptionId = description ? `${switchId}-description` : undefined;
  return (
    <div data-slot="switch-row" className={cn("flex items-start justify-between gap-4", className)}>
      <div className="flex min-w-0 flex-col gap-0.5">
        <label htmlFor={switchId} className="text-control leading-5 font-semibold text-foreground">
          {label}
        </label>
        {description ? (
          <p id={descriptionId} className="text-caption text-text-secondary">
            {description}
          </p>
        ) : null}
      </div>
      <Switch id={switchId} aria-describedby={descriptionId} className="mt-0.5" {...props} />
    </div>
  );
}
