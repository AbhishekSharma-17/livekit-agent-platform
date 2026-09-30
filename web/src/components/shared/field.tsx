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
  /**
   * Sets `aria-required` and the word "Required" in the hint slot (no red
   * asterisk). Prefer marking the optional fields instead (spec 7.4).
   */
  required?: boolean;
  /** Adds a 12 px tertiary "(optional)" after the label (spec 6.2). */
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
 * Form field (docs/ui/DESIGN-SYSTEM.md section 6.2): a 6 px grid of a 13 px
 * label, the control, a 12 px secondary hint and a 13 px destructive error.
 * When `children` is a single
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
  const marker = required ? "Required" : null;
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

  // "(optional)" sits beside the label, not inside it: visual only, so the
  // accessible name stays the field name (a control without `required`
  // already reads as optional to assistive tech).
  const labelNode =
    optional && !required ? (
      <div className="flex flex-wrap items-baseline gap-x-1">
        <Label htmlFor={htmlFor} className="leading-5">
          {label}
        </Label>
        <span data-slot="field-optional" aria-hidden="true" className="text-caption text-text-tertiary">
          (optional)
        </span>
      </div>
    ) : (
      <Label htmlFor={htmlFor} className="leading-5">
        {label}
      </Label>
    );
  const hintNode = hasHint ? (
    <p id={ids.hint} className="text-caption leading-[1.125rem] text-pretty text-text-secondary">
      {marker ? <span className="font-medium">{marker}</span> : null}
      {marker && hint ? <span aria-hidden="true"> · </span> : null}
      {hint}
    </p>
  ) : null;

  const errorNode = hasError ? (
    <p id={ids.error} className="text-label leading-[1.125rem] text-destructive-text">
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
          {labelNode}
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
      {labelNode}
      {control}
      {hintNode}
      {errorNode}
    </div>
  );
}

export interface FieldRowProps {
  /** Columns above 640 px (one column on phones). Default 2. */
  columns?: 2 | 3;
  children: React.ReactNode;
  className?: string;
}

/** Two or three fields side by side; collapses to one column on phones (spec 6.2). */
export function FieldRow({ columns = 2, children, className }: FieldRowProps) {
  return (
    <div
      data-slot="field-row"
      className={cn(
        "grid grid-cols-1 gap-4",
        columns === 2 ? "sm:grid-cols-[repeat(2,minmax(0,1fr))]" : "sm:grid-cols-[repeat(3,minmax(0,1fr))]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * The form-level error block (spec 6.2 "Validation"): server errors after a
 * submit, announced assertively. Renders nothing without a message.
 */
export function FormError({ children, className }: { children?: React.ReactNode; className?: string }) {
  if (children === undefined || children === null || children === false || children === "") return null;
  return (
    <div
      role="alert"
      data-slot="form-error"
      className={cn(
        "rounded border border-destructive-border bg-destructive-subtle px-3 py-2.5 text-label text-destructive-text",
        className,
      )}
    >
      {children}
    </div>
  );
}
