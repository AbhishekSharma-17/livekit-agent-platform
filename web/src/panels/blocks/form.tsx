"use client";

/**
 * `form` block — a JSON-schema form the agent asks the caller to fill in
 * (CONTRACTS-V2 §4.4, the V2-10 → V2-11 wire contract; V5-02/V5-03 moved it
 * onto the generic requestable-block machinery).
 *
 * The block renders from **state**, never from the `request` / legacy `form`
 * RPC: the worker sets `status: "requested"` (with `values` = the prefill)
 * before it sends the RPC, and after a reconnect only the snapshot carries
 * it. The RPC just scrolls here (`composite/requests.ts`). Answers go back
 * through `useBlockRequest` (`composite/use-block-request.ts`): `block_submit
 * {block_id, values}`, or `{block_id, cancelled: true}` — the one path every
 * requestable block's renderer now shares.
 *
 * Schema subset (the worker's `fields_to_schema`): `properties` in field
 * order, each `{title, type: string|number|integer|boolean, format?:
 * date|email|phone, enum?}`, plus `required`; V5-19/V5-23 add two JSON-schema
 * extension keys the worker also emits (`lkap_contracts.ui_protocol`,
 * `FORM_WIDGET_KEY`/`FORM_UPLOAD_KEY` — not exported to the generated
 * contracts, so their literal strings are pinned here and in
 * `panel-blocks.test.tsx`): `"x-lkap-widget": "textarea"` for a longer text
 * field, and `"x-lkap-widget": "file"` + `"x-lkap-upload": {accept,
 * max_files, max_bytes}` for a field the caller fills by sending files.
 *
 * A `file` field's control (`FormFileField`) lives in `./upload.tsx`, not
 * here, and is loaded lazily: it needs `useMaybeRoomContext`
 * (`@livekit/components-react`), and this block — unlike `upload` — is
 * common enough (and rendered in the composer preview and the console's
 * read-only snapshot, `BLOCK_COMPONENTS` in `blocks/index.tsx`) that it must
 * not pull `livekit-client` into every page that renders a panel (the same
 * reason `video`/`document`/`table`/`markdown` are lazy). `formFields`
 * itself needs no room, so parsing a schema with a `file` field, and every
 * other field kind, stays synchronous.
 */
import * as React from "react";
import { Suspense, lazy, useId, useMemo, useState } from "react";

import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { AssetRef, FormBlockState } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { DEFAULT_UPLOAD_ACCEPT, type UploadLimits } from "@/panels/composite/upload";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const FormFileField = lazy(() => import("./upload").then((m) => ({ default: m.FormFileField })));

export type FormFieldKind = "text" | "email" | "phone" | "date" | "number" | "integer" | "boolean" | "select" | "textarea" | "file";

export interface FormFieldSpec {
  name: string;
  label: string;
  kind: FormFieldKind;
  required: boolean;
  options: string[];
  /** `x-lkap-upload`'s limits, only present for `kind === "file"`. */
  upload?: UploadLimits;
}

/** `lkap_contracts.ui_protocol.FORM_WIDGET_KEY` / `FORM_UPLOAD_KEY` (not in the generated contracts — plain constants, not a model). */
const FORM_WIDGET_KEY = "x-lkap-widget";
const FORM_UPLOAD_KEY = "x-lkap-upload";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function uploadSpecOf(raw: unknown): UploadLimits {
  const spec = isRecord(raw) ? raw : {};
  const accept =
    Array.isArray(spec.accept) && spec.accept.every((a) => typeof a === "string") && spec.accept.length > 0
      ? (spec.accept as string[])
      : DEFAULT_UPLOAD_ACCEPT;
  const maxFiles = typeof spec.max_files === "number" && spec.max_files > 0 ? spec.max_files : 1;
  const maxBytes = typeof spec.max_bytes === "number" && spec.max_bytes > 0 ? spec.max_bytes : 10 * 1024 * 1024;
  return { accept, maxFiles, maxBytes };
}

/** Flatten the block's JSON schema into renderable fields, in property order. */
export function formFields(schema: unknown): FormFieldSpec[] {
  if (!isRecord(schema) || !isRecord(schema.properties)) return [];
  const required = new Set(Array.isArray(schema.required) ? schema.required.filter((r) => typeof r === "string") : []);
  return Object.entries(schema.properties).map(([name, raw]) => {
    const prop = isRecord(raw) ? raw : {};
    const options = Array.isArray(prop.enum) ? prop.enum.map((o) => String(o)) : [];
    const widget = typeof prop[FORM_WIDGET_KEY] === "string" ? prop[FORM_WIDGET_KEY] : null;
    let kind: FormFieldKind = "text";
    let upload: UploadLimits | undefined;
    if (widget === "file") {
      kind = "file";
      upload = uploadSpecOf(prop[FORM_UPLOAD_KEY]);
    } else if (widget === "textarea") {
      kind = "textarea";
    } else if (options.length > 0) kind = "select";
    else if (prop.type === "boolean") kind = "boolean";
    else if (prop.type === "integer") kind = "integer";
    else if (prop.type === "number") kind = "number";
    else if (prop.format === "date") kind = "date";
    else if (prop.format === "email") kind = "email";
    else if (prop.format === "phone") kind = "phone";
    const label = typeof prop.title === "string" && prop.title ? prop.title : name;
    return { name, label, kind, required: required.has(name), options, upload };
  });
}

type Draft = Record<string, string | boolean | string[]>;

function toDraft(fields: FormFieldSpec[], values: Record<string, unknown>): Draft {
  const draft: Draft = {};
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === "boolean") draft[field.name] = value === true;
    else if (field.kind === "file") draft[field.name] = Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
    else draft[field.name] = value === undefined || value === null ? "" : String(value);
  }
  return draft;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Validate and coerce the draft into the `values` object the worker expects.
 * Optional empty fields are omitted; numbers are numbers, booleans booleans,
 * a `file` field the stored asset ids it has actually received (never
 * something the caller typed).
 */
export function coerceForm(
  fields: FormFieldSpec[],
  draft: Draft,
): { values: Record<string, unknown>; errors: Record<string, string> } {
  const values: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const raw = draft[field.name];
    if (field.kind === "boolean") {
      values[field.name] = raw === true;
      continue;
    }
    if (field.kind === "file") {
      const ids = Array.isArray(raw) ? raw : [];
      if (ids.length === 0) {
        if (field.required) errors[field.name] = `Attach ${field.label.toLowerCase()}.`;
        continue;
      }
      values[field.name] = ids;
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (text === "") {
      if (field.required) errors[field.name] = `Enter ${field.label.toLowerCase()}.`;
      continue;
    }
    if (field.kind === "number" || field.kind === "integer") {
      const n = Number(text);
      if (!Number.isFinite(n) || (field.kind === "integer" && !Number.isInteger(n))) {
        errors[field.name] = field.kind === "integer" ? "Enter a whole number." : "Enter a number.";
        continue;
      }
      values[field.name] = n;
      continue;
    }
    if (field.kind === "email" && !EMAIL.test(text)) {
      errors[field.name] = "Enter an email address like name@example.com.";
      continue;
    }
    if (field.kind === "select" && !field.options.includes(text)) {
      errors[field.name] = "Choose one of the options.";
      continue;
    }
    values[field.name] = text;
  }
  return { values, errors };
}

const SELECT_CLASSES =
  "border-input dark:bg-input/30 focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:border-destructive h-8 w-full rounded-sm border bg-transparent px-2 text-base outline-none focus-visible:ring-3 md:text-sm";

type AriaProps = Pick<React.AriaAttributes, "aria-describedby" | "aria-invalid" | "aria-required">;

/**
 * `Field` clones its child with the aria wiring; forward it to the real
 * control. Never called for `kind === "file"` — `FormEditor` renders
 * `FormFileField` directly for that one, in a `<fieldset>` instead of a
 * `<Field>` (the same reason `block-config-form.tsx`'s `multiselect`/`list`
 * kinds bypass their own single-input `ConfigFieldControl`).
 */
function FieldControl({
  field,
  id,
  value,
  onChange,
  disabled,
  ...aria
}: {
  field: FormFieldSpec;
  id: string;
  value: string | boolean;
  onChange: (value: string | boolean) => void;
  disabled: boolean;
} & AriaProps) {
  switch (field.kind) {
    case "boolean":
      return (
        <input
          id={id}
          name={field.name}
          type="checkbox"
          checked={value === true}
          disabled={disabled}
          {...aria}
          onChange={(event) => onChange(event.target.checked)}
          className="accent-primary size-4"
        />
      );
    case "select":
      return (
        <select
          id={id}
          name={field.name}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className={SELECT_CLASSES}
          {...aria}
        >
          <option value="">Choose…</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    case "textarea":
      return (
        <Textarea
          id={id}
          name={field.name}
          rows={4}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          {...aria}
        />
      );
    case "file":
      // `FormEditor` never reaches this branch for a `file` field (see the
      // docstring above); kept only so the switch stays exhaustive.
      return null;
    default:
      return (
        <Input
          id={id}
          name={field.name}
          type={
            field.kind === "number" || field.kind === "integer"
              ? "number"
              : field.kind === "date" || field.kind === "email"
                ? field.kind
                : field.kind === "phone"
                  ? "tel"
                  : "text"
          }
          inputMode={
            field.kind === "integer" ? "numeric" : field.kind === "number" ? "decimal" : field.kind === "phone" ? "tel" : undefined
          }
          step={field.kind === "integer" ? 1 : field.kind === "number" ? "any" : undefined}
          autoComplete={field.kind === "email" ? "email" : field.kind === "phone" ? "tel" : "off"}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          {...aria}
        />
      );
  }
}

function formatValue(value: unknown): string {
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (Array.isArray(value)) return value.length === 0 ? "—" : `${value.length} file${value.length === 1 ? "" : "s"}`;
  if (value === undefined || value === null || value === "") return "—";
  return String(value);
}

/**
 * The editable form for one request; remounted (fresh draft **and** fresh
 * `useBlockRequest` — `sending`/`error` reset too) per request, keyed by the
 * question's schema and prefill (`FormBlock`'s `requestKey`).
 */
function FormEditor({
  blockId,
  title,
  fields,
  prefill,
  assets,
  panelAssets,
  perform,
}: {
  blockId: string;
  title: string | null;
  fields: FormFieldSpec[];
  prefill: Record<string, unknown>;
  assets: AssetRef[];
  panelAssets: Map<string, string>;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const baseId = useId();
  const [draft, setDraft] = useState<Draft>(() => toDraft(fields, prefill));
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Mounted only while `status === "requested"` (`FormBlock` below); passing
  // that literal, rather than threading the live status through, is what it
  // is while this component is mounted at all.
  const { sending, error: sendError, submit, cancel } = useBlockRequest(blockId, "requested", perform);

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending) return;
    const { values, errors: found } = coerceForm(fields, draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    void submit(values);
  }

  function onCancel() {
    if (sending) return;
    void cancel();
  }

  return (
    <form data-slot="block-form" noValidate onSubmit={onSubmit} aria-label={title ?? "Form"} className="flex flex-col gap-3">
      {fields.length === 0 ? (
        <PanelEmpty>The agent asked for a form with no fields.</PanelEmpty>
      ) : (
        fields.map((field) => {
          const id = `${baseId}-${field.name}`;
          if (field.kind === "file") {
            // A fieldset, not a `<Field>`: `FormFileField` is a composite
            // control (a button, a hidden input and a file list), not the
            // single focusable element `Field` clones aria props onto — the
            // same reason `block-config-form.tsx`'s `multiselect`/`list`
            // kinds render their own `<fieldset>` too.
            return (
              <fieldset key={field.name} className="flex flex-col gap-1.5">
                <legend className="mb-1 text-sm leading-5 font-medium">
                  {field.label}
                  {field.required && (
                    <span className="text-muted-foreground ml-1.5 text-[0.8125rem] font-normal">Required</span>
                  )}
                </legend>
                <Suspense fallback={<PanelEmpty>Loading…</PanelEmpty>}>
                  <FormFileField
                    field={field}
                    blockId={blockId}
                    assets={assets}
                    panelAssets={panelAssets}
                    disabled={sending !== null}
                    onChange={(ids) => setDraft((prev) => ({ ...prev, [field.name]: ids }))}
                  />
                </Suspense>
                {errors[field.name] && (
                  <p role="alert" className="text-danger-text text-[0.8125rem]">
                    {errors[field.name]}
                  </p>
                )}
              </fieldset>
            );
          }
          return (
            <Field
              key={field.name}
              label={field.label}
              htmlFor={id}
              required={field.required}
              error={errors[field.name]}
              inline={field.kind === "boolean"}
            >
              <FieldControl
                field={field}
                id={id}
                value={draft[field.name] as string | boolean | undefined ?? (field.kind === "boolean" ? false : "")}
                disabled={sending !== null}
                onChange={(value) => setDraft((prev) => ({ ...prev, [field.name]: value }))}
              />
            </Field>
          );
        })
      )}
      {sendError && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {sendError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button type="submit" size="sm" disabled={sending !== null}>
          {sending === "submit" ? "Sending…" : "Send"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={sending !== null}>
          Not now
        </Button>
      </div>
    </form>
  );
}

export function FormBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<FormBlockState>) {
  const status = data.status ?? "idle";
  const fields = useMemo(() => formFields(data.schema), [data.schema]);
  const prefill = useMemo(() => (isRecord(data.values) ? data.values : {}), [data.values]);
  // A new request (new schema or prefill) remounts the editor with a fresh draft.
  const requestKey = useMemo(() => JSON.stringify([data.schema ?? {}, prefill]), [data.schema, prefill]);

  let body: React.ReactNode;
  if (status === "requested") {
    body = (
      <FormEditor
        key={requestKey}
        blockId={spec.id}
        title={title}
        fields={fields}
        prefill={prefill}
        assets={panel.state.assets ?? []}
        panelAssets={panel.assets}
        perform={panel.perform}
      />
    );
  } else if (status === "cancelled") {
    body = <PanelEmpty>You dismissed this without answering.</PanelEmpty>;
  } else if (status === "submitted") {
    const shown = fields.length > 0 ? fields.map((f) => [f.label, prefill[f.name]] as const) : Object.entries(prefill);
    body = (
      <div data-slot="block-form-submitted" className="flex flex-col gap-2.5">
        <p className="flex items-center gap-2">
          <StatusChip tone="success" size="sm" dot>
            Sent
          </StatusChip>
          {typeof data.submitted_at === "number" && (
            <span className="text-muted-foreground text-xs">{formatTime(data.submitted_at)}</span>
          )}
        </p>
        <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
          {shown.map(([label, value]) => (
            <React.Fragment key={String(label)}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 break-words">{formatValue(value)}</dd>
            </React.Fragment>
          ))}
        </dl>
      </div>
    );
  } else {
    body = <PanelEmpty>The agent will ask for details here when it needs them.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {body}
    </BlockFrame>
  );
}
