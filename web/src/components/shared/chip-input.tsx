"use client";

import * as React from "react";
import { XIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Chip input for emails, tags and URLs (docs/ui/DESIGN-SYSTEM.md section 6.2).
 *
 * - Enter, comma and semicolon commit a chip; pasting a list splits it;
 *   leaving the field commits what is typed; Backspace on an empty field
 *   removes the last chip.
 * - Invalid chips are **kept and flagged** with a destructive tint, never
 *   silently dropped. `validate` returns `true` or the reason.
 * - Chips are 26 px pills with an initial and a remove `X`.
 * - Every change is announced through a polite live region.
 */
export interface ChipInputProps {
  values: string[];
  onValuesChange: (values: string[]) => void;
  /** `true` when valid, otherwise the reason ("Not an email address"). */
  validate?: (value: string) => true | string;
  /** Singular noun for announcements, e.g. "email". */
  itemLabel?: string;
  /** Show each chip's first letter. Default true. */
  showInitial?: boolean;
  placeholder?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

const SEPARATORS = new Set(["Enter", ",", ";"]);
const SPLIT = /[\s,;]+/;

/** A basic email check for `validate` (the server stays the real gate). */
export function validateEmail(value: string): true | string {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? true : "Not an email address";
}

export function ChipInput({
  values,
  onValuesChange,
  validate,
  itemLabel = "item",
  showInitial = true,
  placeholder,
  id,
  disabled = false,
  className,
  "aria-label": ariaLabel,
  "aria-describedby": describedBy,
  "aria-invalid": ariaInvalid,
}: ChipInputProps) {
  const [draft, setDraft] = React.useState("");
  const [announcement, setAnnouncement] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);

  const reasonFor = React.useCallback((value: string) => {
    const result = validate?.(value) ?? true;
    return result === true ? null : result;
  }, [validate]);

  const commit = (raw: string) => {
    const incoming = raw
      .split(SPLIT)
      .map((part) => part.trim())
      .filter(Boolean);
    if (incoming.length === 0) return;
    const next = [...values];
    const added: string[] = [];
    for (const value of incoming) {
      if (next.includes(value)) continue;
      next.push(value);
      added.push(value);
    }
    setDraft("");
    if (added.length === 0) return;
    onValuesChange(next);
    const invalid = added.filter((value) => reasonFor(value) !== null);
    const parts = [added.length === 1 ? `Added ${added[0]}` : `Added ${added.length} ${itemLabel}s`];
    if (invalid.length === 1) parts.push(`${invalid[0]}: ${reasonFor(invalid[0])}`);
    else if (invalid.length > 1) parts.push(`${invalid.length} need fixing`);
    setAnnouncement(`${parts.join(". ")}.`);
  };

  const remove = (value: string) => {
    onValuesChange(values.filter((v) => v !== value));
    setAnnouncement(`Removed ${value}.`);
    inputRef.current?.focus();
  };

  const invalidCount = values.filter((value) => reasonFor(value) !== null).length;

  return (
    <div
      data-slot="chip-input"
      data-invalid={invalidCount > 0 || ariaInvalid ? "" : undefined}
      onClick={() => inputRef.current?.focus()}
      className={cn(
        "flex min-h-9 w-full cursor-text flex-wrap items-center gap-1.5 rounded border border-input bg-card px-1.5 py-[4px] transition-[border-color,box-shadow] duration-(--duration-fast) hover:border-border-strong has-[input:focus-visible]:border-brand has-[input:focus-visible]:shadow-focus data-invalid:border-destructive-solid",
        disabled && "cursor-not-allowed bg-muted opacity-50",
        className,
      )}
    >
      <ul aria-label={ariaLabel ? `${ariaLabel}: ${values.length} added` : undefined} className="contents">
        {values.map((value) => {
          const reason = reasonFor(value);
          return (
            <li
              key={value}
              data-slot="chip"
              data-invalid={reason ? "" : undefined}
              className={cn(
                "inline-flex h-[26px] max-w-full items-center gap-1.5 rounded-pill border pr-1 pl-1 text-label",
                reason
                  ? "border-destructive-border bg-destructive-subtle text-destructive-text"
                  : "border-border bg-muted text-foreground",
                !showInitial && "pl-2.5",
              )}
              title={reason ?? undefined}
            >
              {showInitial ? (
                <span
                  aria-hidden="true"
                  className={cn(
                    "inline-flex size-5 shrink-0 items-center justify-center rounded-pill text-tab font-semibold uppercase",
                    reason ? "bg-card" : "bg-muted-strong text-text-secondary",
                  )}
                >
                  {value.charAt(0)}
                </span>
              ) : null}
              <span className="truncate">{value}</span>
              {reason ? <span className="sr-only">({reason})</span> : null}
              <button
                type="button"
                disabled={disabled}
                aria-label={`Remove ${value}`}
                onClick={(event) => {
                  event.stopPropagation();
                  remove(value);
                }}
                className="inline-flex size-5 shrink-0 items-center justify-center rounded-pill text-current opacity-70 transition-opacity hover:opacity-100"
              >
                <XIcon aria-hidden="true" className="size-3" />
              </button>
            </li>
          );
        })}
      </ul>
      <input
        ref={inputRef}
        id={id}
        value={draft}
        disabled={disabled}
        placeholder={values.length === 0 ? placeholder : undefined}
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        aria-invalid={invalidCount > 0 || ariaInvalid ? true : undefined}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (SEPARATORS.has(event.key)) {
            if (draft.trim() !== "" || event.key !== "Enter") event.preventDefault();
            commit(draft);
          } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
            event.preventDefault();
            remove(values[values.length - 1]);
          }
        }}
        onPaste={(event) => {
          const text = event.clipboardData.getData("text");
          if (SPLIT.test(text.trim())) {
            event.preventDefault();
            commit(`${draft} ${text}`);
          }
        }}
        onBlur={() => commit(draft)}
        className="h-[26px] min-w-24 flex-1 bg-transparent px-1.5 text-control text-foreground outline-none placeholder:text-text-tertiary focus-visible:outline-none disabled:cursor-not-allowed"
      />
      <span role="status" aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </div>
  );
}
