"use client";

import * as React from "react";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FieldSpec } from "@/contracts/lkap-contracts";

export type FieldValue = string | number | boolean;
export type FieldValues = Record<string, FieldValue>;

/**
 * Renders one `ProviderSpec.fields`/`secret_fields` list as form controls,
 * driven entirely by the registry (docs/CONTRACTS.md §4 `FieldSpec`) — this
 * is the "forms are generated from the registry" requirement. No provider
 * gets bespoke UI; adding a provider to `providers.json` is enough to get a
 * working form.
 *
 * Controlled component: the caller owns the value (usually
 * `ProviderRef.fields`, or a credential's `secrets` draft) so it can be
 * embedded either inside a react-hook-form `Controller` (provider fields) or
 * plain `useState` (the credential dialog, which is intentionally kept out
 * of the big form so a submitted secret can never linger in RHF state).
 *
 * `secret` fields render as masked, write-only inputs — CONTRACTS §4 is
 * explicit that secret values are never sent back by the api, so this
 * component never receives a real stored value to prefill; `secretsMasked`
 * lets the caller show "•••• (unchanged)" placeholders when editing without
 * forcing rotation.
 */
export function RegistryForm({
  fields,
  values,
  onChange,
  secretsMasked = false,
  idPrefix,
}: {
  fields: FieldSpec[];
  values: FieldValues;
  onChange: (name: string, value: FieldValue) => void;
  secretsMasked?: boolean;
  idPrefix?: string;
}) {
  const autoId = useId();
  const prefix = idPrefix ?? autoId;

  if (fields.length === 0) {
    return null;
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {fields.filter((field) => fieldConditionMet(field, values)).map((field) => (
        <RegistryField
          key={field.name}
          field={field}
          value={values[field.name]}
          onChange={(value) => onChange(field.name, value)}
          secretsMasked={secretsMasked}
          fieldId={`${prefix}-${field.name}`}
        />
      ))}
    </div>
  );
}

/**
 * `FieldSpec.condition` is `"siblingName=value"` — show this field only when
 * another field in the same `fields`/`secret_fields` list currently equals
 * that value (docs/CONTRACTS.md §4). No MVP registry entry uses this yet,
 * but the schema allows it (e.g. a future `vertexai=true` toggle), so the
 * generator honours it rather than silently ignoring conditional fields.
 */
function fieldConditionMet(field: FieldSpec, values: FieldValues): boolean {
  if (!field.condition) return true;
  const [siblingName, expected] = field.condition.split("=");
  if (!siblingName) return true;
  const actual = values[siblingName];
  return String(actual ?? "") === (expected ?? "");
}

function RegistryField({
  field,
  value,
  onChange,
  secretsMasked,
  fieldId,
}: {
  field: FieldSpec;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  secretsMasked: boolean;
  fieldId: string;
}) {
  const isFullWidth = field.type === "json" || field.type === "string";

  return (
    <div className={isFullWidth ? "sm:col-span-2" : undefined}>
      <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-foreground">
        {field.label}
        {field.required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </label>
      <RegistryFieldControl
        field={field}
        value={value}
        onChange={onChange}
        secretsMasked={secretsMasked}
        fieldId={fieldId}
      />
      {field.help ? <p className="mt-1 text-xs text-muted-foreground">{field.help}</p> : null}
    </div>
  );
}

function RegistryFieldControl({
  field,
  value,
  onChange,
  secretsMasked,
  fieldId,
}: {
  field: FieldSpec;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  secretsMasked: boolean;
  fieldId: string;
}) {
  switch (field.type) {
    case "boolean": {
      const checked = typeof value === "boolean" ? value : Boolean(field.default ?? false);
      return (
        <div className="flex h-8 items-center">
          <Switch id={fieldId} checked={checked} onCheckedChange={(next) => onChange(next)} />
        </div>
      );
    }

    case "number": {
      const numberValue = typeof value === "number" ? value : (field.default as number | undefined);
      return (
        <Input
          id={fieldId}
          type="number"
          value={numberValue ?? ""}
          placeholder={field.placeholder ?? undefined}
          onChange={(event) => {
            const raw = event.target.value;
            onChange(raw === "" ? 0 : Number(raw));
          }}
        />
      );
    }

    case "enum": {
      const options = field.options ?? [];
      const current = typeof value === "string" ? value : (field.default as string | undefined) ?? "";
      return (
        <Select value={current} onValueChange={(next) => onChange(next)}>
          <SelectTrigger id={fieldId} className="w-full">
            <SelectValue placeholder={field.placeholder ?? "Choose…"} />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }

    case "secret": {
      const stringValue = typeof value === "string" ? value : "";
      return (
        <Input
          id={fieldId}
          type="password"
          autoComplete="new-password"
          value={stringValue}
          placeholder={secretsMasked && stringValue === "" ? "•••• (unchanged)" : (field.placeholder ?? undefined)}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    }

    case "json": {
      const stringValue = typeof value === "string" ? value : (field.default as string | undefined) ?? "";
      return (
        <textarea
          id={fieldId}
          className="min-h-24 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          value={stringValue}
          placeholder={field.placeholder ?? undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    }

    case "model":
    case "string":
    default: {
      const stringValue = typeof value === "string" ? value : value !== undefined ? String(value) : "";
      return (
        <Input
          id={fieldId}
          type="text"
          value={stringValue}
          placeholder={field.placeholder ?? (field.default !== null && field.default !== undefined ? String(field.default) : undefined)}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    }
  }
}

/**
 * Default `fields` values for a provider, used when switching to a new
 * provider (or opening the credential dialog) so controlled inputs always
 * have a defined value.
 */
export function defaultFieldValues(fields: FieldSpec[]): FieldValues {
  const values: FieldValues = {};
  for (const field of fields) {
    if (field.default !== null && field.default !== undefined) {
      values[field.name] = field.default;
    } else if (field.type === "boolean") {
      values[field.name] = false;
    } else if (field.type === "number") {
      values[field.name] = 0;
    } else {
      values[field.name] = "";
    }
  }
  return values;
}
