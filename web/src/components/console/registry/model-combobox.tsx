"use client";

import * as React from "react";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import type { ModelSpec } from "@/contracts/lkap-contracts";

/**
 * "Model combobox with suggestions + free text" (IMPLEMENTATION_PLAN
 * W1-WEB-CONSOLE): a native `<input list>` datalist. LiveKit Inference's
 * model catalog "churns" (docs/CONTRACTS.md §6 validation rules — a model
 * not in `spec.models` is a warning, not an error), so free text always has
 * to work; a plain input with suggestions gets that for free without a
 * `cmdk`/popover dependency this package doesn't own the manifest for.
 */
export function ModelCombobox({
  models,
  value,
  onChange,
  placeholder,
  id,
}: {
  models: ModelSpec[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Forwarded to the underlying `<input>` so a caller's `<label htmlFor>` resolves. */
  id?: string;
}) {
  const listId = useId();
  const selected = models.find((m) => m.id === value);

  return (
    <div>
      <Input
        id={id}
        list={listId}
        value={value}
        placeholder={placeholder ?? "Model id"}
        onChange={(event) => onChange(event.target.value)}
      />
      <datalist id={listId}>
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </datalist>
      {selected ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {selected.label}
          {selected.supports_video ? " · vision" : ""}
          {selected.note ? ` · ${selected.note}` : ""}
        </p>
      ) : null}
    </div>
  );
}
