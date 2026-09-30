"use client";

import * as React from "react";
import { EyeIcon, EyeOffIcon, KeyRoundIcon } from "lucide-react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { InputWithIcon } from "./search-field";

/**
 * Account password with a show/hide toggle (docs/ui/DESIGN-SYSTEM.md
 * section 6.2). The toggle is a real button with a pressed state, so screen
 * readers hear "Show password, toggle button".
 */
export function PasswordInput({ className, ...props }: Omit<React.ComponentProps<"input">, "type">) {
  const [visible, setVisible] = React.useState(false);
  return (
    <div data-slot="password-input" className="relative w-full min-w-0">
      <Input type={visible ? "text" : "password"} className={cn("pr-10", className)} {...props} />
      <button
        type="button"
        aria-label="Show password"
        aria-pressed={visible}
        onClick={() => setVisible((v) => !v)}
        className="absolute top-1/2 right-1 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-sm text-text-tertiary transition-colors duration-(--duration-fast) hover:bg-muted hover:text-foreground"
      >
        {visible ? <EyeOffIcon aria-hidden="true" /> : <EyeIcon aria-hidden="true" />}
      </button>
    </div>
  );
}

export interface SecretInputProps extends Omit<React.ComponentProps<"input">, "type" | "value" | "defaultValue"> {
  /** The new value being typed (never the saved one: the api does not send it). */
  value: string;
  /** A value is already stored. The field stays empty and says so. */
  saved?: boolean;
  /** Shown while a value is stored, e.g. "Saved · ends in 3f9a". */
  savedHint?: string;
}

/**
 * Write-only secret (section 6.2, 9 "Secrets are write-only"): API keys and
 * tokens are masked while typed and never shown again after saving. There is
 * no reveal toggle. When a value is stored the field stays empty with a
 * "Saved" placeholder; typing replaces the stored value on save.
 */
export function SecretInput({ value, saved = false, savedHint, placeholder, className, ...props }: SecretInputProps) {
  return (
    <InputWithIcon
      icon={KeyRoundIcon}
      type="password"
      value={value}
      autoComplete="new-password"
      spellCheck={false}
      data-slot="secret-input"
      data-saved={saved ? "" : undefined}
      placeholder={saved ? (savedHint ?? "Saved. Enter a new value to replace it.") : placeholder}
      className={cn("font-mono", className)}
      {...props}
    />
  );
}
