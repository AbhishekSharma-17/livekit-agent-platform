import * as React from "react";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export interface FieldProps {
  label: React.ReactNode;
  /** The control's id; the label binds to it and hint/error ids derive from it. */
  htmlFor: string;
  hint?: React.ReactNode;
  /** Field-level error (§6): shown under the control; the control gets `aria-invalid`. */
  error?: React.ReactNode;
  /** Marked with the word "Required" in the hint slot (no red asterisk). */
  required?: boolean;
  /** Marked with the word "Optional" in the hint slot. */
  optional?: boolean;
  children: React.ReactNode;
  /** Label + hint on the left, control on the right (switches, short selects). */
  inline?: boolean;
  className?: string;
}

/** Ids a `Field` assigns for `aria-describedby` wiring. */
export function fieldIds(htmlFor: string) {
  return { hint: `${htmlFor}-hint`, error: `${htmlFor}-error` };
}

function mergeIds(...ids: Array<string | undefined>): string | undefined {
  const joined = ids.filter(Boolean).join(" ");
  return joined || undefined;
}

/**
 * Form field (docs/UI_UX_SPEC.md §2.7, §6). When `children` is a single
 * element, `aria-describedby` (hint + error) and `aria-invalid` are wired onto
 * it automatically; for composite controls, use `fieldIds(htmlFor)` yourself.
 */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  required = false,
  optional = false,
  children,
  inline = false,
  className,
}: FieldProps) {
  const ids = fieldIds(htmlFor);
  const marker = required ? "Required" : optional ? "Optional" : null;
  const hasHint = Boolean(marker || hint);
  const hasError = error !== undefined && error !== null && error !== false && error !== "";

  let control = children;
  // A plain layout wrapper (`<div className="relative">` around an input and
  // its icon) is not the control: ARIA state on it is invalid (axe
  // `aria-allowed-attr`) and never reaches the input. Such callers wire
  // `fieldIds(htmlFor)` onto the input themselves.
  const isLayoutWrapper =
    React.isValidElement(children) &&
    typeof children.type === "string" &&
    !["input", "select", "textarea", "button"].includes(children.type);
  if (React.isValidElement<Record<string, unknown>>(children) && !isLayoutWrapper) {
    const existing = children.props["aria-describedby"] as string | undefined;
    control = React.cloneElement(children, {
      "aria-describedby": mergeIds(existing, hasHint ? ids.hint : undefined, hasError ? ids.error : undefined),
      "aria-invalid": hasError ? true : (children.props["aria-invalid"] as boolean | undefined),
      "aria-required": required ? true : (children.props["aria-required"] as boolean | undefined),
    });
  }

  const hintNode = hasHint ? (
    <p id={ids.hint} className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
      {marker ? <span className="font-medium">{marker}</span> : null}
      {marker && hint ? <span aria-hidden="true"> · </span> : null}
      {hint}
    </p>
  ) : null;

  const errorNode = hasError ? (
    <p id={ids.error} className="text-[0.8125rem] leading-[1.125rem] text-danger-text">
      {error}
    </p>
  ) : null;

  if (inline) {
    return (
      <div
        data-slot="field"
        data-inline=""
        data-invalid={hasError ? "" : undefined}
        className={cn("grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-1.5", className)}
      >
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label htmlFor={htmlFor} className="leading-5">
            {label}
          </Label>
          {hintNode}
        </div>
        <div className="flex items-center pt-0.5">{control}</div>
        {errorNode ? <div className="col-span-2">{errorNode}</div> : null}
      </div>
    );
  }

  return (
    <div
      data-slot="field"
      data-invalid={hasError ? "" : undefined}
      className={cn("flex flex-col gap-1.5", className)}
    >
      <Label htmlFor={htmlFor} className="leading-5">
        {label}
      </Label>
      {control}
      {hintNode}
      {errorNode}
    </div>
  );
}
