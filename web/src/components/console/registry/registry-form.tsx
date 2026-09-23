"use client";

import * as React from "react";
import { useId } from "react";
import { EyeIcon, EyeOffIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field } from "@/components/shared/field";
import { useCatalog, type CatalogKind } from "@/hooks/useCatalog";
import { cn } from "@/lib/utils";
import type { CatalogSpec, FieldSpec, ProviderSpec } from "@/contracts/lkap-contracts";

export type FieldValue = string | number | boolean;
export type FieldValues = Record<string, FieldValue>;

/**
 * Lets a field render a vendor catalog combobox instead of free text
 * (V2-13; UI_UX_SPEC-V2-AMENDMENTS §2.2/§2.3 "avatar pickers"). Threaded
 * through from the provider slot editor, which is the only caller with both
 * the provider (`catalog`) and the slot's chosen `credential_id` in scope.
 *
 * A field is a catalog picker when either is true:
 *   1. `field.type === "catalog"` (an explicit `catalog_kind`), or
 *   2. `provider.kind === "avatar"` with a `catalog` and a field name that
 *      looks like the vendor's avatar/face id (`avatar_id`, `*.face_id`,
 *      `*.persona_id`). No registry entry sets `catalog_kind` on its avatar
 *      id field today (confirmed against `contracts/providers.py`), so (2)
 *      is a name-convention fallback, not a hidden per-vendor special case —
 *      it applies uniformly to any avatar provider with a `catalog`. Flagged
 *      in the V2-13 report as worth promoting to an explicit `catalog_kind`
 *      on those fields (a one-line ask to V2-05) once this ships.
 */
export interface CatalogFieldContext {
  providerId: string;
  kind: ProviderSpec["kind"];
  catalog: CatalogSpec | null | undefined;
  credentialId: string | null;
}

const AVATAR_ID_FIELD_NAME = /(^|\.)(avatar_id|face_id|persona_id)$/;

function catalogKindFor(field: FieldSpec, ctx: CatalogFieldContext | undefined): CatalogKind | null {
  if (!ctx?.catalog) return null;
  const kinds = ctx.catalog.kinds ?? [];
  if (field.type === "catalog" && field.catalog_kind && kinds.includes(field.catalog_kind)) {
    return field.catalog_kind;
  }
  if (ctx.kind === "avatar" && kinds.includes("avatars") && AVATAR_ID_FIELD_NAME.test(field.name)) {
    return "avatars";
  }
  return null;
}

export interface RegistryFormProps {
  fields: FieldSpec[];
  values: FieldValues;
  onChange: (name: string, value: FieldValue) => void;
  /** Editing a stored credential: blank secret inputs mean "keep the stored value". */
  secretsMasked?: boolean;
  idPrefix?: string;
  /**
   * The provider's `capabilities.voices`. When non-empty, a `voice`/`voice_id`
   * text field renders as a `Select` with a "Custom…" escape (§7.5 item 4).
   */
  voices?: string[];
  /** Per-field error messages (field name → message). */
  errors?: Record<string, string | undefined>;
  /** One column instead of two on desktop (the credential sheet). */
  singleColumn?: boolean;
  /** Enables the catalog combobox for the fields it matches (see `CatalogFieldContext`). */
  catalogContext?: CatalogFieldContext;
}

const CUSTOM = "__custom__";

/** Common BCP-47 codes for the `language` field; free text stays available via "Custom…". */
export const COMMON_LANGUAGES: { code: string; label: string }[] = [
  { code: "en", label: "English" },
  { code: "en-US", label: "English (US)" },
  { code: "en-GB", label: "English (UK)" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "it", label: "Italian" },
  { code: "pt", label: "Portuguese" },
  { code: "pt-BR", label: "Portuguese (Brazil)" },
  { code: "nl", label: "Dutch" },
  { code: "hi", label: "Hindi" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "zh", label: "Chinese" },
  { code: "ar", label: "Arabic" },
  { code: "ru", label: "Russian" },
  { code: "multi", label: "Multilingual" },
];

const VOICE_FIELD_NAMES = new Set(["voice", "voice_id"]);

/**
 * Renders one `ProviderSpec.fields`/`secret_fields` list as form controls,
 * driven entirely by the registry (docs/CONTRACTS.md §4 `FieldSpec`) — no
 * provider gets bespoke UI. Each control sits in a WP-0 `Field` (label,
 * "Required"/help hint, inline error).
 *
 * Controlled: the caller owns the values (`ProviderRef.fields`, or the
 * credential sheet's `useState` draft — secrets are never put into the big
 * react-hook-form state). `secret` fields are masked, write-only inputs with
 * show/hide; the api never sends secret values back, so `secretsMasked` only
 * changes the placeholder ("Leave blank to keep").
 *
 * v2 field types: `catalog` (a vendor catalog id, or a name-matched avatar
 * id field — see `CatalogFieldContext`; both need `catalogContext`) and
 * `file` render as a combobox and a text input respectively.
 */
export function RegistryForm({
  fields,
  values,
  onChange,
  secretsMasked = false,
  idPrefix,
  voices,
  errors,
  singleColumn = false,
  catalogContext,
}: RegistryFormProps) {
  const autoId = useId();
  const prefix = idPrefix ?? autoId;

  if (fields.length === 0) {
    return null;
  }

  return (
    <div className={cn("grid gap-4", !singleColumn && "sm:grid-cols-2")}>
      {fields
        .filter((field) => fieldConditionMet(field, values))
        .map((field) => {
          const fieldId = `${prefix}-${field.name}`;
          const wide = singleColumn || field.type === "json" || field.type === "string" || field.type === "catalog";
          return (
            <RegistryField
              key={field.name}
              field={field}
              value={values[field.name]}
              onChange={(value) => onChange(field.name, value)}
              secretsMasked={secretsMasked}
              fieldId={fieldId}
              voices={voices}
              error={errors?.[field.name]}
              className={wide && !singleColumn ? "sm:col-span-2" : undefined}
              catalogContext={catalogContext}
            />
          );
        })}
    </div>
  );
}

/**
 * `FieldSpec.condition` is `"siblingName=value"` — show this field only when
 * a sibling in the same list currently equals that value (CONTRACTS §4).
 */
function fieldConditionMet(field: FieldSpec, values: FieldValues): boolean {
  if (!field.condition) return true;
  const [siblingName, expected] = field.condition.split("=");
  if (!siblingName) return true;
  const actual = values[siblingName];
  return String(actual ?? "") === (expected ?? "");
}

function fieldHint(field: FieldSpec, secretsMasked: boolean): React.ReactNode {
  if (field.type === "secret" && secretsMasked) {
    return field.help ? `Leave blank to keep the stored value. ${field.help}` : "Leave blank to keep the stored value.";
  }
  if (field.type === "catalog") {
    return field.help ?? "Paste the id from the vendor's dashboard.";
  }
  if (field.type === "file") {
    return field.help ?? "Path to the file on the agent worker.";
  }
  return field.help ?? undefined;
}

function RegistryField({
  field,
  value,
  onChange,
  secretsMasked,
  fieldId,
  voices,
  error,
  className,
  catalogContext,
}: {
  field: FieldSpec;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  secretsMasked: boolean;
  fieldId: string;
  voices?: string[];
  error?: string;
  className?: string;
  catalogContext?: CatalogFieldContext;
}) {
  const required = Boolean(field.required) && !(field.type === "secret" && secretsMasked);
  return (
    <Field
      label={field.label}
      htmlFor={fieldId}
      hint={fieldHint(field, secretsMasked)}
      required={required}
      error={error}
      inline={field.type === "boolean"}
      className={className}
    >
      <RegistryFieldControl
        field={field}
        value={value}
        onChange={onChange}
        secretsMasked={secretsMasked}
        fieldId={fieldId}
        voices={voices}
        catalogContext={catalogContext}
      />
    </Field>
  );
}

function RegistryFieldControl({
  field,
  value,
  onChange,
  secretsMasked,
  fieldId,
  voices,
  catalogContext,
  ...aria
}: {
  field: FieldSpec;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  secretsMasked: boolean;
  fieldId: string;
  voices?: string[];
  catalogContext?: CatalogFieldContext;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const stringValue =
    typeof value === "string" ? value : value !== undefined && value !== null ? String(value) : "";

  const catalogKind = catalogKindFor(field, catalogContext);
  if (catalogKind && catalogContext) {
    return (
      <CatalogPickerField
        id={fieldId}
        providerId={catalogContext.providerId}
        kind={catalogKind}
        credentialId={catalogContext.credentialId}
        value={stringValue}
        onChange={onChange}
        {...aria}
      />
    );
  }

  switch (field.type) {
    case "boolean": {
      const checked = typeof value === "boolean" ? value : Boolean(field.default ?? false);
      return <Switch id={fieldId} checked={checked} onCheckedChange={(next) => onChange(next)} {...aria} />;
    }

    case "number": {
      const numberValue = typeof value === "number" ? value : (field.default as number | undefined);
      return (
        <Input
          id={fieldId}
          type="number"
          inputMode="decimal"
          step="any"
          value={numberValue ?? ""}
          placeholder={field.placeholder ?? undefined}
          onChange={(event) => {
            const raw = event.target.value;
            onChange(raw === "" ? 0 : Number(raw));
          }}
          className="tabular-nums"
          {...aria}
        />
      );
    }

    case "enum": {
      const options = field.options ?? [];
      const current = typeof value === "string" && value !== "" ? value : ((field.default as string | undefined) ?? undefined);
      return (
        <Select value={current || undefined} onValueChange={(next) => onChange(next)}>
          <SelectTrigger id={fieldId} className="w-full" {...aria}>
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

    case "secret":
      return (
        <SecretInput
          id={fieldId}
          value={stringValue}
          placeholder={secretsMasked ? "•••• (unchanged)" : (field.placeholder ?? undefined)}
          onChange={onChange}
          {...aria}
        />
      );

    case "json":
      return <JsonTextarea id={fieldId} value={stringValue || String(field.default ?? "")} placeholder={field.placeholder} onChange={onChange} {...aria} />;

    case "model":
    case "string":
    case "catalog":
    case "file":
    default: {
      if (field.type === "string" && VOICE_FIELD_NAMES.has(field.name) && voices && voices.length > 0) {
        return (
          <ListWithCustom
            id={fieldId}
            value={stringValue}
            options={voices.map((voice) => ({ value: voice, label: voice }))}
            placeholder={field.default !== null && field.default !== undefined ? String(field.default) : "Choose a voice"}
            onChange={onChange}
            customLabel="Custom voice…"
            {...aria}
          />
        );
      }
      if (field.type === "string" && field.name === "language") {
        return (
          <ListWithCustom
            id={fieldId}
            value={stringValue}
            options={COMMON_LANGUAGES.map((lang) => ({ value: lang.code, label: `${lang.label} · ${lang.code}` }))}
            placeholder={field.default !== null && field.default !== undefined ? String(field.default) : "Choose a language"}
            onChange={onChange}
            customLabel="Other code…"
            {...aria}
          />
        );
      }
      return (
        <Input
          id={fieldId}
          type="text"
          value={stringValue}
          className={field.type === "catalog" || field.type === "model" ? "font-mono text-[0.8125rem]" : undefined}
          placeholder={
            field.placeholder ?? (field.default !== null && field.default !== undefined ? String(field.default) : undefined)
          }
          onChange={(event) => onChange(event.target.value)}
          {...aria}
        />
      );
    }
  }
}

/** A `Select` over known values with a "Custom…" escape to free text. */
function ListWithCustom({
  id,
  value,
  options,
  placeholder,
  onChange,
  customLabel,
  ...aria
}: {
  id: string;
  value: string;
  options: { value: string; label: string }[];
  placeholder: string;
  onChange: (value: string) => void;
  customLabel: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const known = value === "" || options.some((option) => option.value === value);
  const [custom, setCustom] = React.useState(!known);
  // Focus the text input only when the user picked "Custom…", not on first render.
  const [focusCustom, setFocusCustom] = React.useState(false);

  if (custom) {
    return (
      <div className="flex items-center gap-2">
        <Input id={id} type="text" value={value} autoFocus={focusCustom} onChange={(e) => onChange(e.target.value)} {...aria} />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="shrink-0"
          onClick={() => {
            setCustom(false);
            if (!options.some((option) => option.value === value)) onChange("");
          }}
        >
          Pick from list
        </Button>
      </div>
    );
  }

  return (
    <Select
      value={value || undefined}
      onValueChange={(next) => {
        if (next === CUSTOM) {
          setCustom(true);
          setFocusCustom(true);
          return;
        }
        onChange(next);
      }}
    >
      <SelectTrigger id={id} className="w-full" {...aria}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
        <SelectItem value={CUSTOM}>{customLabel}</SelectItem>
      </SelectContent>
    </Select>
  );
}

function SecretInput({
  id,
  value,
  placeholder,
  onChange,
  ...aria
}: {
  id: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const [shown, setShown] = React.useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={shown ? "text" : "password"}
        autoComplete="new-password"
        spellCheck={false}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="pr-10 font-mono text-[0.8125rem]"
        {...aria}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="absolute top-1/2 right-1 -translate-y-1/2"
        aria-label={shown ? "Hide value" : "Show value"}
        aria-pressed={shown}
        onClick={() => setShown((s) => !s)}
      >
        {shown ? <EyeOffIcon aria-hidden="true" /> : <EyeIcon aria-hidden="true" />}
      </Button>
    </div>
  );
}

function JsonTextarea({
  id,
  value,
  placeholder,
  onChange,
  ...aria
}: {
  id: string;
  value: string;
  placeholder?: string | null;
  onChange: (value: string) => void;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const [formatError, setFormatError] = React.useState<string | null>(null);
  return (
    <div className="flex flex-col gap-1.5">
      <textarea
        id={id}
        className="min-h-24 w-full rounded-sm border border-input bg-transparent px-2.5 py-2 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
        value={value}
        placeholder={placeholder ?? undefined}
        spellCheck={false}
        onChange={(event) => {
          setFormatError(null);
          onChange(event.target.value);
        }}
        {...aria}
      />
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            if (value.trim() === "") return;
            try {
              onChange(JSON.stringify(JSON.parse(value), null, 2));
              setFormatError(null);
            } catch {
              setFormatError("Not valid JSON — check commas and quotes.");
            }
          }}
        >
          Format
        </Button>
        {formatError ? (
          <span role="status" className="text-[0.8125rem] text-danger-text">
            {formatError}
          </span>
        ) : null}
      </div>
    </div>
  );
}

const MANUAL_ENTRY = "__manual__";

/** Best-effort image key sniffing over a catalog item's vendor-shaped `meta` (no normalised key exists — `CatalogItem.meta` is the raw vendor JSON, `api/src/lkap_api/catalogs/base.py::parse_items`). */
const IMAGE_META_KEYS = [
  "image_url",
  "imageUrl",
  "thumbnail_url",
  "thumbnailUrl",
  "preview_url",
  "previewUrl",
  "avatar_url",
  "avatarUrl",
  "photo_url",
  "photoUrl",
  "face_url",
  "picture",
  "thumbnail",
  "image",
] as const;

function previewImageUrl(meta: Record<string, unknown> | undefined): string | null {
  if (!meta) return null;
  for (const key of IMAGE_META_KEYS) {
    const value = meta[key];
    if (typeof value === "string" && /^https?:\/\//.test(value)) return value;
  }
  return null;
}

/**
 * A vendor catalog id field (V2-13): a `Select` of `GET
 * /providers/{id}/catalog` items with a preview thumbnail when the vendor's
 * item carries one, and a "Paste id manually…" escape that becomes the
 * default view once the catalog has no items (no key yet, the vendor call
 * failed, or a v1-style free-text id already stored). `CatalogResponse.error`
 * never fails the field — V2-06's "a failed vendor call must never break the
 * page" — it renders as a quiet note under the input.
 */
function CatalogPickerField({
  id,
  providerId,
  kind,
  credentialId,
  value,
  onChange,
  ...aria
}: {
  id: string;
  providerId: string;
  kind: CatalogKind;
  credentialId: string | null;
  value: string;
  onChange: (value: string) => void;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}) {
  const { data, isLoading, isError } = useCatalog(providerId, kind, credentialId);
  const items = data?.items ?? [];
  const knownId = value === "" || items.some((item) => item.id === value);
  // `null` = no explicit user choice yet — while the catalog is still
  // loading, `items` is always `[]`, so deciding "manual" from `knownId` at
  // that instant would wrongly stick a valid pre-existing id in manual mode
  // forever (state, once initialised, does not recompute on its own).
  // Falls back to manual only once loading has actually finished and the id
  // still isn't known.
  const [manualOverride, setManualOverride] = React.useState<boolean | null>(null);
  const manual = manualOverride ?? (!isLoading && !knownId);

  const note = isError ? "Couldn't load the catalog — paste the id instead." : (data?.error ?? null);

  if (manual || (!isLoading && items.length === 0)) {
    return (
      <div className="flex flex-col gap-1.5">
        <Input
          id={id}
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Paste the id from the vendor's dashboard"
          className="font-mono text-[0.8125rem]"
          {...aria}
        />
        {items.length > 0 ? (
          <button
            type="button"
            onClick={() => setManualOverride(false)}
            className="self-start rounded-xs text-xs font-medium text-muted-foreground underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            Choose from the catalog instead
          </button>
        ) : null}
        {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      </div>
    );
  }

  const selected = items.find((item) => item.id === value);
  const preview = previewImageUrl(selected?.meta);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element -- vendor-hosted thumbnail, not a local asset
          <img src={preview} alt="" className="size-8 shrink-0 rounded-full border border-border object-cover" />
        ) : null}
        <Select
          value={knownId && value !== "" ? value : undefined}
          onValueChange={(next) => (next === MANUAL_ENTRY ? setManualOverride(true) : onChange(next))}
        >
          <SelectTrigger id={id} className="w-full min-w-0 flex-1" {...aria}>
            <SelectValue placeholder={isLoading ? "Loading catalog…" : "Choose…"} />
          </SelectTrigger>
          <SelectContent>
            {items.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.label}
              </SelectItem>
            ))}
            <SelectItem value={MANUAL_ENTRY}>Paste id manually…</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
    </div>
  );
}

/**
 * Default `fields` values for a provider, used when switching to a new
 * provider (or opening the credential sheet) so controlled inputs always
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
