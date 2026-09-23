"use client";

/**
 * `form` block — a JSON-schema form the agent asks the caller to fill in
 * (CONTRACTS-V2 §4.4, the V2-10 → V2-11 wire contract).
 *
 * The block renders from **state**, never from the `form` RPC: the worker sets
 * `status: "requested"` (with `values` = the prefill) before it sends the RPC,
 * and after a reconnect only the snapshot carries it. The RPC just scrolls
 * here (`composite/requests.ts`). Answers go back through `perform`:
 * `form_submit {block_id, values}`, or `{block_id, cancelled: true}`.
 *
 * Schema subset (the worker's `fields_to_schema`): `properties` in field
 * order, each `{title, type: string|number|integer|boolean, format?: date|email,
 * enum?}`, plus `required`.
 */
import * as React from "react";
import { useId, useMemo, useState } from "react";

import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { FormBlockState } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

export type FormFieldKind = "text" | "email" | "date" | "number" | "integer" | "boolean" | "select";

export interface FormFieldSpec {
  name: string;
  label: string;
  kind: FormFieldKind;
  required: boolean;
  options: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Flatten the block's JSON schema into renderable fields, in property order. */
export function formFields(schema: unknown): FormFieldSpec[] {
  if (!isRecord(schema) || !isRecord(schema.properties)) return [];
  const required = new Set(Array.isArray(schema.required) ? schema.required.filter((r) => typeof r === "string") : []);
  return Object.entries(schema.properties).map(([name, raw]) => {
    const prop = isRecord(raw) ? raw : {};
    const options = Array.isArray(prop.enum) ? prop.enum.map((o) => String(o)) : [];
    let kind: FormFieldKind = "text";
    if (options.length > 0) kind = "select";
    else if (prop.type === "boolean") kind = "boolean";
    else if (prop.type === "integer") kind = "integer";
    else if (prop.type === "number") kind = "number";
    else if (prop.format === "date") kind = "date";
    else if (prop.format === "email") kind = "email";
    const label = typeof prop.title === "string" && prop.title ? prop.title : name;
    return { name, label, kind, required: required.has(name), options };
  });
}

type Draft = Record<string, string | boolean>;

function toDraft(fields: FormFieldSpec[], values: Record<string, unknown>): Draft {
  const draft: Draft = {};
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === "boolean") draft[field.name] = value === true;
    else draft[field.name] = value === undefined || value === null ? "" : String(value);
  }
  return draft;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Validate and coerce the draft into the `values` object the worker expects.
 * Optional empty fields are omitted; numbers are numbers, booleans booleans.
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

/** `Field` clones its child with the aria wiring; forward it to the real control. */
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
                : "text"
          }
          inputMode={field.kind === "integer" ? "numeric" : field.kind === "number" ? "decimal" : undefined}
          step={field.kind === "integer" ? 1 : field.kind === "number" ? "any" : undefined}
          autoComplete={field.kind === "email" ? "email" : "off"}
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
  if (value === undefined || value === null || value === "") return "—";
  return String(value);
}

/** The editable form for one request; remounted (fresh draft) per request. */
function FormEditor({
  blockId,
  title,
  fields,
  prefill,
  perform,
}: {
  blockId: string;
  title: string | null;
  fields: FormFieldSpec[];
  prefill: Record<string, unknown>;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const baseId = useId();
  const [draft, setDraft] = useState<Draft>(() => toDraft(fields, prefill));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sending, setSending] = useState<"submit" | "cancel" | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  async function send(
    payload: { block_id: string; values: Record<string, unknown> } | { block_id: string; cancelled: true },
  ) {
    setSendError(null);
    try {
      const result = (await perform({ action: "form_submit", payload })) as
        | { ok?: boolean; error?: string | null }
        | undefined;
      if (result && result.ok === false) {
        setSendError(result.error || "The agent didn't accept the form. Try again.");
        setSending(null);
      }
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Couldn't reach the agent. Try again.");
      setSending(null);
    }
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending) return;
    const { values, errors: found } = coerceForm(fields, draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSending("submit");
    void send({ block_id: blockId, values });
  }

  function onCancel() {
    if (sending) return;
    setSending("cancel");
    void send({ block_id: blockId, cancelled: true });
  }

  return (
    <form data-slot="block-form" noValidate onSubmit={onSubmit} aria-label={title ?? "Form"} className="flex flex-col gap-3">
      {fields.length === 0 ? (
        <PanelEmpty>The agent asked for a form with no fields.</PanelEmpty>
      ) : (
        fields.map((field) => {
          const id = `${baseId}-${field.name}`;
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
                value={draft[field.name] ?? (field.kind === "boolean" ? false : "")}
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
        perform={panel.perform}
      />
    );
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
