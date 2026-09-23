"use client";

/**
 * One block's settings in the composer: title, id, and a form generated from
 * the block type's config schema (`BLOCK_CATALOG[type].configFields`, see
 * `@/panels/blocks/catalog`). Controlled: every change goes straight back to
 * `config.panel` through `onChange`.
 */
import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { BlockSpecForm, PanelLayoutForm } from "@/components/console/lib/schemas";
import type { TableColumn } from "@/contracts/lkap-contracts";
import { BLOCK_CATALOG, TABLE_COLUMN_TYPES, type BlockConfigField } from "@/panels/blocks/catalog";

import { setBlockConfig, updateBlock } from "./composer-model";

const COLUMN_TYPE_LABEL: Record<TableColumn["type"] & string, string> = {
  string: "Text",
  number: "Number",
  boolean: "Yes / no",
  date: "Date",
};

function isColumns(value: unknown): value is TableColumn[] {
  return Array.isArray(value) && value.every((c) => typeof c === "object" && c !== null && "key" in c);
}

function ColumnsEditor({
  idBase,
  value,
  onChange,
}: {
  idBase: string;
  value: TableColumn[];
  onChange: (next: TableColumn[]) => void;
}) {
  function patch(index: number, next: Partial<TableColumn>) {
    onChange(value.map((column, i) => (i === index ? { ...column, ...next } : column)));
  }
  return (
    <div className="flex flex-col gap-2" data-slot="columns-editor">
      {value.length > 0 && (
        <div className="text-muted-foreground grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_7.5rem_2rem] gap-2 text-xs font-medium">
          <span>Key</span>
          <span>Label</span>
          <span>Type</span>
          <span className="sr-only">Remove</span>
        </div>
      )}
      {value.map((column, index) => (
        <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_7.5rem_2rem] items-center gap-2">
          <Input
            aria-label={`Column ${index + 1} key`}
            value={column.key}
            onChange={(event) => patch(index, { key: event.target.value.replace(/\s+/g, "_") })}
            className="font-mono text-sm"
          />
          <Input
            aria-label={`Column ${index + 1} label`}
            value={column.label}
            onChange={(event) => patch(index, { label: event.target.value })}
          />
          <Select
            value={column.type ?? "string"}
            onValueChange={(type) => patch(index, { type: type as TableColumn["type"] })}
          >
            <SelectTrigger aria-label={`Column ${index + 1} type`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TABLE_COLUMN_TYPES.map((type) => (
                <SelectItem key={type} value={type ?? "string"}>
                  {COLUMN_TYPE_LABEL[type ?? "string"]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove column ${column.label || column.key || index + 1}`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            <Icon as={Trash2Icon} size="sm" />
          </Button>
        </div>
      ))}
      <Button
        id={`${idBase}-add-column`}
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...value, { key: `col_${value.length + 1}`, label: `Column ${value.length + 1}`, type: "string" }])}
      >
        <Icon as={PlusIcon} size="sm" />
        Add column
      </Button>
    </div>
  );
}

function ConfigFieldControl({
  field,
  id,
  value,
  onChange,
}: {
  field: BlockConfigField;
  id: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  switch (field.kind) {
    case "boolean":
      return <Switch id={id} checked={value === true} onCheckedChange={(checked) => onChange(checked)} />;
    case "integer":
      return (
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={field.min}
          step={1}
          className="w-28"
          value={typeof value === "number" ? value : field.default}
          onChange={(event) => {
            const n = Math.floor(Number(event.target.value));
            onChange(Number.isFinite(n) && n >= (field.min ?? 0) ? n : field.default);
          }}
        />
      );
    case "url":
      return (
        <Input
          id={id}
          type="url"
          inputMode="url"
          placeholder="https://"
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value.trim())}
        />
      );
    case "select":
      return (
        <Select value={typeof value === "string" ? value : field.default} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {field.options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    case "columns":
      return null; // rendered as a fieldset by `BlockConfigForm`
  }
}

function urlError(field: BlockConfigField, value: unknown): string | undefined {
  if (field.kind !== "url" || typeof value !== "string" || value === "") return undefined;
  try {
    return new URL(value).protocol === "https:" ? undefined : "Use an https:// link.";
  } catch {
    return "Enter a full https:// link.";
  }
}

export function BlockConfigForm({
  panel,
  index,
  onChange,
  idError,
}: {
  panel: PanelLayoutForm;
  index: number;
  onChange: (next: PanelLayoutForm) => void;
  /** A validation message for this block's id (duplicate, bad characters). */
  idError?: string;
}) {
  const block: BlockSpecForm | undefined = panel.blocks[index];
  const baseId = React.useId();
  if (!block) return null;
  const entry = BLOCK_CATALOG[block.type];

  return (
    <div data-slot="block-config-form" className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" htmlFor={`${baseId}-title`} optional hint={entry.defaultTitle ? `Default: ${entry.defaultTitle}` : "No heading by default"}>
          <Input
            id={`${baseId}-title`}
            value={block.title ?? ""}
            placeholder={entry.defaultTitle ?? ""}
            onChange={(event) => onChange(updateBlock(panel, index, { title: event.target.value === "" ? null : event.target.value }))}
          />
        </Field>
        <Field
          label="Block id"
          htmlFor={`${baseId}-id`}
          hint="The agent's tools name the block by this id."
          error={idError}
        >
          <Input
            id={`${baseId}-id`}
            value={block.id}
            className="font-mono text-sm"
            data-issue-path={`panel.blocks.${index}.id`}
            onChange={(event) => onChange(updateBlock(panel, index, { id: event.target.value.replace(/\s+/g, "_") }))}
          />
        </Field>
      </div>
      {entry.configFields.map((field) => {
        const id = `${baseId}-${field.key}`;
        const value = block.config[field.key];
        if (field.kind === "columns") {
          // A group of controls, so a fieldset + legend rather than one label.
          return (
            <fieldset key={field.key} className="flex flex-col gap-1.5" aria-describedby={`${id}-hint`}>
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <ColumnsEditor
                idBase={id}
                value={isColumns(value) ? value : []}
                onChange={(next) => onChange(setBlockConfig(panel, index, field, next))}
              />
              {field.hint ? (
                <p id={`${id}-hint`} className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
                  {field.hint}
                </p>
              ) : null}
            </fieldset>
          );
        }
        return (
          <Field
            key={field.key}
            label={field.label}
            htmlFor={id}
            hint={field.hint}
            inline={field.kind === "boolean"}
            error={urlError(field, value)}
          >
            <ConfigFieldControl
              field={field}
              id={id}
              value={value}
              onChange={(next) => onChange(setBlockConfig(panel, index, field, next))}
            />
          </Field>
        );
      })}
      <p className="text-muted-foreground text-[0.8125rem]">Filled by {entry.filledBy}.</p>
    </div>
  );
}
