"use client";

import * as React from "react";

import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { fieldsFromSchema, fieldValue, type FieldDescriptor, type JsonSchema } from "./schema";

/**
 * A small schema → form renderer on the WP-0 primitives (V2-16). Every
 * property of the schema renders a labelled control; a caller replaces any
 * field's control with `renderers[name]` (the flow builder does this for tool
 * and variable pickers) and hides fields with `hidden`.
 */

export interface FieldRendererProps {
  field: FieldDescriptor;
  /** The control's DOM id (the label points at it). */
  id: string;
  value: unknown;
  onChange: (next: unknown) => void;
  /** `aria-describedby` / `aria-invalid` for composite controls (a single element gets them from `Field`). */
  describedBy: string;
  invalid: boolean;
}

export interface SchemaFormProps {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  /** Prefix for control ids (unique per form on the page). */
  idPrefix: string;
  hidden?: readonly string[];
  readOnly?: readonly string[];
  multiline?: readonly string[];
  labels?: Readonly<Record<string, string>>;
  hints?: Readonly<Record<string, React.ReactNode>>;
  /** Field-level messages (errors first) keyed by property name. */
  errors?: Readonly<Record<string, string | undefined>>;
  renderers?: Readonly<Record<string, (props: FieldRendererProps) => React.ReactNode>>;
  className?: string;
}

const UNSET = "__unset__";

/** What `Field` clones onto its single child; forwarded to the real control. */
interface AriaProps {
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-required"?: boolean;
}

export function SchemaForm({
  schema,
  value,
  onChange,
  idPrefix,
  hidden = [],
  readOnly = [],
  multiline = [],
  labels = {},
  hints = {},
  errors = {},
  renderers = {},
  className,
}: SchemaFormProps) {
  const fields = React.useMemo(() => fieldsFromSchema(schema, { multiline }), [schema, multiline]);
  const set = (name: string, next: unknown) => onChange({ ...value, [name]: next });

  return (
    <div className={className ?? "flex flex-col gap-4"} data-slot="schema-form">
      {fields
        .filter((field) => !hidden.includes(field.name))
        .map((field) => {
          const id = `${idPrefix}-${field.name}`;
          const current = fieldValue(field, value);
          const error = errors[field.name];
          const custom = renderers[field.name];
          const label = labels[field.name] ?? field.label;
          const control = custom ? (
            custom({
              field,
              id,
              value: current,
              onChange: (next) => set(field.name, next),
              describedBy: `${id}-hint${error ? ` ${id}-error` : ""}`,
              invalid: Boolean(error),
            })
          ) : (
            <FieldControl
              field={field}
              id={id}
              value={current}
              readOnly={readOnly.includes(field.name)}
              onChange={(next) => set(field.name, next)}
            />
          );
          return (
            <div key={field.name} data-field-name={field.name} data-field-kind={field.kind}>
              <Field
                label={label}
                htmlFor={id}
                hint={hints[field.name] ?? field.description}
                error={error}
                required={field.required && !readOnly.includes(field.name) && field.kind !== "const"}
                inline={field.kind === "boolean" && !field.nullable && !custom}
              >
                {control}
              </Field>
            </div>
          );
        })}
    </div>
  );
}

function FieldControl({
  field,
  id,
  value,
  readOnly,
  onChange,
  ...aria
}: {
  field: FieldDescriptor;
  id: string;
  value: unknown;
  readOnly: boolean;
  onChange: (next: unknown) => void;
} & AriaProps) {
  switch (field.kind) {
    case "const":
      return <Input id={id} value={String(field.schema.const ?? value ?? "")} readOnly {...aria} />;
    case "text":
      return (
        <Input
          id={id}
          value={typeof value === "string" ? value : ""}
          readOnly={readOnly}
          onChange={(event) => onChange(emptyToNull(field, event.target.value))}
          {...aria}
        />
      );
    case "textarea":
      return (
        <Textarea
          id={id}
          className="min-h-24 resize-y"
          value={typeof value === "string" ? value : ""}
          readOnly={readOnly}
          onChange={(event) => onChange(emptyToNull(field, event.target.value))}
          {...aria}
        />
      );
    case "number":
    case "integer":
      return (
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          step={field.kind === "integer" ? 1 : "any"}
          min={field.schema.minimum}
          max={field.schema.maximum}
          value={typeof value === "number" ? String(value) : ""}
          readOnly={readOnly}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "") onChange(field.nullable ? null : undefined);
            else onChange(field.kind === "integer" ? Math.trunc(Number(raw)) : Number(raw));
          }}
          {...aria}
        />
      );
    case "boolean":
      if (field.nullable) {
        const current = value === true ? "true" : value === false ? "false" : UNSET;
        return (
          <Select
            value={current}
            disabled={readOnly}
            onValueChange={(next) => onChange(next === UNSET ? null : next === "true")}
          >
            <SelectTrigger id={id} className="w-full" {...aria}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNSET}>Default</SelectItem>
              <SelectItem value="true">On</SelectItem>
              <SelectItem value="false">Off</SelectItem>
            </SelectContent>
          </Select>
        );
      }
      return (
        <Switch id={id} checked={value === true} disabled={readOnly} onCheckedChange={onChange} {...aria} />
      );
    case "enum": {
      const options = field.options ?? [];
      const current = value === null || value === undefined ? UNSET : String(value);
      return (
        <Select
          value={current}
          disabled={readOnly}
          onValueChange={(next) => onChange(next === UNSET ? null : next)}
        >
          <SelectTrigger id={id} className="w-full" {...aria}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {field.nullable ? <SelectItem value={UNSET}>None</SelectItem> : null}
            {options.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }
    case "string-list":
      return <StringListInput id={id} value={value} readOnly={readOnly} onChange={onChange} {...aria} />;
    case "json":
      return <JsonInput id={id} value={value} readOnly={readOnly} onChange={onChange} {...aria} />;
  }
}

function emptyToNull(field: FieldDescriptor, raw: string): string | null {
  return raw === "" && field.nullable ? null : raw;
}

/** Comma-separated list of strings (the default for `list[str]`). */
function StringListInput({
  id,
  value,
  readOnly,
  onChange,
  ...aria
}: {
  id: string;
  value: unknown;
  readOnly: boolean;
  onChange: (next: unknown) => void;
} & AriaProps) {
  const list = Array.isArray(value) ? value.map(String) : [];
  const [text, setText] = React.useState(list.join(", "));
  const joined = list.join(", ");
  React.useEffect(() => setText(joined), [joined]);
  return (
    <Input
      id={id}
      value={text}
      readOnly={readOnly}
      placeholder="Comma-separated"
      onChange={(event) => setText(event.target.value)}
      onBlur={() =>
        onChange(
          text
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
        )
      }
      {...aria}
    />
  );
}

/** Raw JSON for shapes with no dedicated control; applied on blur when it parses. */
function JsonInput({
  id,
  value,
  readOnly,
  onChange,
  ...aria
}: {
  id: string;
  value: unknown;
  readOnly: boolean;
  onChange: (next: unknown) => void;
} & AriaProps) {
  const serialized = value === undefined ? "" : JSON.stringify(value, null, 2);
  const [text, setText] = React.useState(serialized);
  const [invalid, setInvalid] = React.useState(false);
  React.useEffect(() => setText(serialized), [serialized]);
  return (
    <div className="flex flex-col gap-1">
      <Textarea
        id={id}
        className="min-h-16 resize-y font-mono text-xs"
        value={text}
        readOnly={readOnly}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
        onBlur={() => {
          if (text.trim() === "") {
            setInvalid(false);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(text));
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }}
        {...aria}
        aria-invalid={invalid || undefined}
      />
      {invalid ? <p className="text-[0.8125rem] text-danger-text">Not valid JSON — the change was not applied.</p> : null}
    </div>
  );
}
