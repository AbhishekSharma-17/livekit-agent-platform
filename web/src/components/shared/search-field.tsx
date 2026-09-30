"use client";

import * as React from "react";
import { SearchIcon, XIcon, type LucideIcon } from "lucide-react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * Input with a leading icon (docs/ui/DESIGN-SYSTEM.md section 6.2): a 16 px
 * tertiary icon 11 px from the left, the input padded 34 px. For search and
 * key fields.
 */
export interface InputWithIconProps extends React.ComponentProps<"input"> {
  icon: LucideIcon;
  /** Trailing content inside the field (a clear button). */
  trailing?: React.ReactNode;
  wrapperClassName?: string;
}

export function InputWithIcon({ icon: Glyph, trailing, className, wrapperClassName, ...props }: InputWithIconProps) {
  return (
    <div data-slot="input-with-icon" className={cn("relative w-full min-w-0", wrapperClassName)}>
      <Glyph aria-hidden="true" className="pointer-events-none absolute top-1/2 left-[11px] -translate-y-1/2 text-text-tertiary" />
      <Input className={cn("pl-[34px]", trailing ? "pr-9" : undefined, className)} {...props} />
      {trailing ? <div className="absolute inset-y-0 right-1 flex items-center">{trailing}</div> : null}
    </div>
  );
}

export interface SearchFieldProps extends Omit<React.ComponentProps<"input">, "value" | "onChange" | "type"> {
  value: string;
  onValueChange: (value: string) => void;
  /** Required: the field has no visible label. E.g. "Search agents". */
  "aria-label": string;
  wrapperClassName?: string;
}

/**
 * Search field (section 6.2): `type="search"` with a leading `Search` icon.
 * Escape clears it, and a trailing `X` appears only while it has a value.
 * Filter synchronously as the person types. Escape on an empty field is left
 * alone, so it still closes a surrounding dialog.
 */
export function SearchField({
  value,
  onValueChange,
  placeholder = "Search…",
  onKeyDown,
  className,
  wrapperClassName,
  ...props
}: SearchFieldProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const clear = () => {
    onValueChange("");
    inputRef.current?.focus();
  };
  return (
    <InputWithIcon
      ref={inputRef}
      icon={SearchIcon}
      type="search"
      role="searchbox"
      value={value}
      placeholder={placeholder}
      autoComplete="off"
      spellCheck={false}
      onChange={(event) => onValueChange(event.target.value)}
      onKeyDown={(event) => {
        onKeyDown?.(event);
        if (event.defaultPrevented) return;
        if (event.key === "Escape" && value !== "") {
          event.preventDefault();
          event.stopPropagation();
          onValueChange("");
        }
      }}
      className={cn("[&::-webkit-search-cancel-button]:appearance-none", className)}
      wrapperClassName={cn("max-w-80", wrapperClassName)}
      data-slot="search-field"
      trailing={
        value !== "" ? (
          <button
            type="button"
            aria-label="Clear search"
            onClick={clear}
            className="inline-flex size-7 items-center justify-center rounded-sm text-text-tertiary transition-colors duration-(--duration-fast) hover:bg-muted hover:text-foreground"
          >
            <XIcon aria-hidden="true" className="size-3.5" />
          </button>
        ) : undefined
      }
      {...props}
    />
  );
}
