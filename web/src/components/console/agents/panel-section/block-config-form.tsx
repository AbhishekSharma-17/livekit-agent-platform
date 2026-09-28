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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { BlockSpecForm, PanelLayoutForm } from "@/components/console/lib/schemas";
import type { TableColumn } from "@/contracts/lkap-contracts";
import { BLOCK_CATALOG, DETAILS_FIELD_TYPES, LANGUAGE_OPTIONS, NOTEBOOK_SECTION_KINDS, TABLE_COLUMN_TYPES, type BlockConfigField } from "@/panels/blocks/catalog";
import { normalizeHost } from "@/panels/blocks/types";

import { setBlockConfig, updateBlock } from "./composer-model";

const COLUMN_TYPE_LABEL: Record<TableColumn["type"] & string, string> = {
  string: "Text",
  number: "Number",
  boolean: "Yes / no",
  date: "Date",
};

const DETAILS_TYPE_LABEL: Record<(typeof DETAILS_FIELD_TYPES)[number], string> = {
  string: "Text",
  number: "Number",
  date: "Date",
  money: "Money",
  phone: "Phone",
  email: "Email",
  badge: "Badge",
};

/** `key`/`id` fields read as identifiers: no spaces. Everything else is free text. */
const IDENTIFIER_ITEM_KEYS = new Set(["key", "id"]);

/** Radix `Select` needs a non-empty value; this stands for "no board chosen yet" (V6-12). */
const NO_CANVAS_BOARD = "__none__";

function isColumns(value: unknown): value is TableColumn[] {
  return Array.isArray(value) && value.every((c) => typeof c === "object" && c !== null && "key" in c);
}

function isRecordArray(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every((v) => typeof v === "object" && v !== null && !Array.isArray(v));
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === "string");
}

/**
 * A list of small objects (`details.fields`, `steps.steps`): one row per
 * item, one control per `itemKeys` entry. `type` (details only) gets the
 * fixed select of `DetailsItem.type`; everything else is a text input.
 */
function ListEditor({
  idBase,
  itemKeys,
  value,
  onChange,
}: {
  idBase: string;
  itemKeys: readonly string[];
  value: Record<string, unknown>[];
  onChange: (next: Record<string, unknown>[]) => void;
}) {
  function patch(index: number, key: string, next: unknown) {
    onChange(value.map((item, i) => (i === index ? { ...item, [key]: next } : item)));
  }
  function blank(): Record<string, unknown> {
    const item: Record<string, unknown> = {};
    for (const key of itemKeys) item[key] = key === "type" ? "string" : "";
    return item;
  }
  return (
    <div className="flex flex-col gap-2" data-slot="list-editor">
      {value.map((item, index) => (
        <div key={index} className="flex flex-wrap items-center gap-2">
          {itemKeys.map((key) => {
            const label = key === "id" ? "ID" : key === "key" ? "Key" : key === "label" ? "Label" : key === "type" ? "Type" : key;
            if (key === "type") {
              const current = typeof item[key] === "string" ? (item[key] as string) : "string";
              return (
                <Select key={key} value={current} onValueChange={(next) => patch(index, key, next)}>
                  <SelectTrigger aria-label={`Row ${index + 1} ${label}`} className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DETAILS_FIELD_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {DETAILS_TYPE_LABEL[type]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              );
            }
            return (
              <Input
                key={key}
                aria-label={`Row ${index + 1} ${label}`}
                value={typeof item[key] === "string" ? (item[key] as string) : ""}
                placeholder={label}
                onChange={(event) =>
                  patch(index, key, IDENTIFIER_ITEM_KEYS.has(key) ? event.target.value.replace(/\s+/g, "_") : event.target.value)
                }
                className={IDENTIFIER_ITEM_KEYS.has(key) ? "w-32 font-mono text-sm" : "min-w-32 flex-1"}
              />
            );
          })}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove row ${index + 1}`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            <Icon as={Trash2Icon} size="sm" />
          </Button>
        </div>
      ))}
      <Button
        id={`${idBase}-add-row`}
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...value, blank()])}
      >
        <Icon as={PlusIcon} size="sm" />
        Add row
      </Button>
    </div>
  );
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

/**
 * Several values ticked from a fixed option list (`upload.accept`, V5-19;
 * the editor itself is V5-23's job, `docs/v5/_asks.md` #131). Unlike
 * `ColumnsEditor`/`ListEditor` this never grows past `options.length` rows,
 * so it is a plain checkbox list, not an "Add row" editor. Unticking the
 * last checked option is refused rather than left silently unchecked in the
 * UI — `UploadBlockConfig.accept` requires at least one type
 * (`validate_accept`, "accept at least one file type").
 */
function MultiselectEditor({
  idBase,
  options,
  value,
  onChange,
}: {
  idBase: string;
  options: readonly { value: string; label: string }[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2" data-slot="multiselect-editor">
      {options.map((option) => {
        const checked = value.includes(option.value);
        const inputId = `${idBase}-${option.value.replace(/[^a-z0-9]+/gi, "-")}`;
        return (
          <div key={option.value} className="flex items-center gap-2">
            <Checkbox
              id={inputId}
              checked={checked}
              onCheckedChange={(next) => {
                if (next === true) {
                  if (!checked) onChange([...value, option.value]);
                  return;
                }
                // Refuse unticking the last option: the schema requires at least one.
                if (checked && value.length > 1) onChange(value.filter((v) => v !== option.value));
              }}
            />
            <Label htmlFor={inputId} className="cursor-pointer text-sm font-normal">
              {option.label}
            </Label>
          </div>
        );
      })}
    </div>
  );
}

/**
 * A site allowlist (`link.allowed_hosts`, `cards.image_hosts`, V5-43;
 * ask #310): one row per site, `example.com` or `*.example.com`, validated
 * with the same `normalizeHost` the renderers re-check against
 * (`@/panels/blocks/types`, a TS port of `ui_protocol.normalize_host`). An
 * empty list is allowed here — `setBlockConfig` drops the key entirely, and
 * a `link` block with no `allowed_hosts` at all 422s at save
 * (`panel.blocks[i].config.allowed_hosts`) until at least one is added,
 * which is the save-time signal ask #310 asks for rather than a client-side
 * "add one to save" block.
 */
function HostsEditor({
  idBase,
  value,
  onChange,
}: {
  idBase: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2" data-slot="hosts-editor">
      {value.map((host, index) => {
        const trimmed = host.trim();
        const invalid = trimmed !== "" && normalizeHost(trimmed) === null;
        return (
          <div key={index} className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <Input
                aria-label={`Site ${index + 1}`}
                aria-invalid={invalid ? true : undefined}
                placeholder="example.com"
                value={host}
                onChange={(event) => onChange(value.map((h, i) => (i === index ? event.target.value : h)))}
                className="max-w-64 font-mono text-sm"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove site ${index + 1}`}
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                <Icon as={Trash2Icon} size="sm" />
              </Button>
            </div>
            {invalid && (
              <p className="text-danger-text text-[0.75rem]">Write it like example.com or *.example.com</p>
            )}
          </div>
        );
      })}
      <Button
        id={`${idBase}-add-site`}
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...value, ""])}
      >
        <Icon as={PlusIcon} size="sm" />
        Add site
      </Button>
    </div>
  );
}

/**
 * A `notebook`'s sections (V6-10, D-V6-15): id, title and a `kind` restricted
 * to the four real section kinds — the plain `ListEditor` treats every
 * `itemKeys` entry as free text, which would let a builder type a `kind` the
 * worker refuses at save.
 */
function NotebookSectionsEditor({
  idBase,
  value,
  onChange,
  canvasBlocks,
}: {
  idBase: string;
  value: Record<string, unknown>[];
  onChange: (next: Record<string, unknown>[]) => void;
  /** `canvas`-type blocks of the same panel, for an `ink` section's board picker (V6-12). */
  canvasBlocks: readonly BlockSpecForm[];
}) {
  function patch(index: number, key: string, next: unknown) {
    onChange(
      value.map((item, i) => {
        if (i !== index) return item;
        const patched = { ...item, [key]: next };
        // A board only ever shows on an ink section (the api's own rule): changing away
        // from ink drops it, so the row never saves an invalid combination.
        if (key === "kind" && next !== "ink") delete patched.canvas_block_id;
        return patched;
      }),
    );
  }
  return (
    <div className="flex flex-col gap-2" data-slot="notebook-sections-editor">
      {value.map((section, index) => {
        const id = typeof section.id === "string" ? section.id : "";
        const sectionTitle = typeof section.title === "string" ? section.title : "";
        const kind = typeof section.kind === "string" ? section.kind : "text";
        const canvasBlockId = typeof section.canvas_block_id === "string" ? section.canvas_block_id : "";
        return (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <Input
              aria-label={`Section ${index + 1} id`}
              placeholder="ID"
              value={id}
              onChange={(event) => patch(index, "id", event.target.value.replace(/\s+/g, "_"))}
              className="w-32 font-mono text-sm"
            />
            <Input
              aria-label={`Section ${index + 1} title`}
              placeholder="Title"
              value={sectionTitle}
              onChange={(event) => patch(index, "title", event.target.value)}
              className="min-w-32 flex-1"
            />
            <Select value={kind} onValueChange={(next) => patch(index, "kind", next)}>
              <SelectTrigger aria-label={`Section ${index + 1} kind`} className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {NOTEBOOK_SECTION_KINDS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {kind === "ink" && (
              <Select value={canvasBlockId || NO_CANVAS_BOARD} onValueChange={(next) => patch(index, "canvas_block_id", next === NO_CANVAS_BOARD ? "" : next)}>
                <SelectTrigger aria-label={`Section ${index + 1} board`} className="w-44">
                  <SelectValue placeholder="No board yet" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_CANVAS_BOARD}>No board yet</SelectItem>
                  {canvasBlocks.map((block) => (
                    <SelectItem key={block.id} value={block.id}>
                      {block.title || BLOCK_CATALOG.canvas.label} · {block.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove section ${index + 1}`}
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              <Icon as={Trash2Icon} size="sm" />
            </Button>
          </div>
        );
      })}
      <Button
        id={`${idBase}-add-section`}
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...value, { id: `section_${value.length + 1}`, title: "", kind: "text" }])}
      >
        <Icon as={PlusIcon} size="sm" />
        Add section
      </Button>
    </div>
  );
}

/**
 * A `layout`'s children (V6-10, D-V6-18): each row picks another block of
 * this same panel — never this `layout` itself, never another `layout` (a
 * layout cannot hold another layout, `layout_issues`) — plus an optional tab
 * label. `otherBlocks` is already filtered to that choice set.
 */
function LayoutChildrenEditor({
  idBase,
  value,
  onChange,
  otherBlocks,
}: {
  idBase: string;
  value: Record<string, unknown>[];
  onChange: (next: Record<string, unknown>[]) => void;
  otherBlocks: readonly BlockSpecForm[];
}) {
  function patch(index: number, key: string, next: unknown) {
    onChange(value.map((item, i) => (i === index ? { ...item, [key]: next } : item)));
  }
  const available = otherBlocks.filter((block) => !value.some((child) => child.block_id === block.id));
  return (
    <div className="flex flex-col gap-2" data-slot="layout-children-editor">
      {value.map((child, index) => {
        const blockId = typeof child.block_id === "string" ? child.block_id : "";
        const label = typeof child.label === "string" ? child.label : "";
        const spec = otherBlocks.find((block) => block.id === blockId);
        return (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <Select value={blockId} onValueChange={(next) => patch(index, "block_id", next)}>
              <SelectTrigger aria-label={`Block ${index + 1}`} className="w-56">
                <SelectValue placeholder="Choose a block" />
              </SelectTrigger>
              <SelectContent>
                {spec && !available.includes(spec) && (
                  <SelectItem value={spec.id}>{spec.title || BLOCK_CATALOG[spec.type].label}</SelectItem>
                )}
                {available.map((block) => (
                  <SelectItem key={block.id} value={block.id}>
                    {block.title || BLOCK_CATALOG[block.type].label} · {block.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              aria-label={`Block ${index + 1} tab label`}
              placeholder="Tab label (optional)"
              value={label}
              onChange={(event) => patch(index, "label", event.target.value === "" ? null : event.target.value)}
              className="min-w-32 flex-1"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove block ${index + 1}`}
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              <Icon as={Trash2Icon} size="sm" />
            </Button>
          </div>
        );
      })}
      <Button
        id={`${idBase}-add-child`}
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        disabled={available.length === 0}
        onClick={() => {
          const next = available[0];
          if (next) onChange([...value, { block_id: next.id, label: null }]);
        }}
      >
        <Icon as={PlusIcon} size="sm" />
        Add a block
      </Button>
      {available.length === 0 && value.length === 0 && (
        <p className="text-muted-foreground text-[0.8125rem]">Add another block to this panel first.</p>
      )}
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
      return (
        <Switch
          id={id}
          checked={value === true}
          disabled={field.disabled}
          onCheckedChange={(checked) => onChange(checked)}
        />
      );
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
    case "text":
      return (
        <Textarea
          id={id}
          rows={3}
          maxLength={field.maxLength}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      );
    case "language":
      // Translated captions are not available yet (ask #203(1)): the picker
      // is shown, disabled, at "None" rather than hidden — a builder can see
      // the setting exists and why it does nothing yet, instead of wondering
      // where it went.
      return (
        <Select value="none" disabled>
          <SelectTrigger id={id} className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None</SelectItem>
            {LANGUAGE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    case "columns":
    case "multiselect":
    case "hosts":
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
        if (field.kind === "list") {
          return (
            <fieldset key={field.key} className="flex flex-col gap-1.5" aria-describedby={`${id}-hint`}>
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <ListEditor
                idBase={id}
                itemKeys={field.itemKeys}
                value={isRecordArray(value) ? value : []}
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
        if (field.kind === "sections") {
          const canvasBlocks = panel.blocks.filter((other) => other.type === "canvas");
          return (
            <fieldset key={field.key} className="flex flex-col gap-1.5" aria-describedby={`${id}-hint`}>
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <NotebookSectionsEditor
                idBase={id}
                value={isRecordArray(value) ? value : []}
                onChange={(next) => onChange(setBlockConfig(panel, index, field, next))}
                canvasBlocks={canvasBlocks}
              />
              {field.hint ? (
                <p id={`${id}-hint`} className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
                  {field.hint}
                </p>
              ) : null}
            </fieldset>
          );
        }
        if (field.kind === "children") {
          const otherBlocks = panel.blocks.filter((other) => other.id !== block.id && other.type !== "layout");
          return (
            <fieldset
              key={field.key}
              className="flex flex-col gap-1.5"
              aria-describedby={`${id}-hint`}
              data-issue-path={`panel.blocks.${index}.config.children`}
            >
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <LayoutChildrenEditor
                idBase={id}
                value={isRecordArray(value) ? value : []}
                onChange={(next) => onChange(setBlockConfig(panel, index, field, next))}
                otherBlocks={otherBlocks}
              />
              {field.hint ? (
                <p id={`${id}-hint`} className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
                  {field.hint}
                </p>
              ) : null}
            </fieldset>
          );
        }
        if (field.kind === "multiselect") {
          return (
            <fieldset key={field.key} className="flex flex-col gap-1.5" aria-describedby={`${id}-hint`}>
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <MultiselectEditor
                idBase={id}
                options={field.options}
                value={isStringArray(value) ? value : [...field.default]}
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
        if (field.kind === "hosts") {
          return (
            <fieldset
              key={field.key}
              className="flex flex-col gap-1.5"
              aria-describedby={`${id}-hint`}
              data-issue-path={`panel.blocks.${index}.config.${field.key}`}
            >
              <legend className="mb-1.5 text-sm leading-5 font-medium">{field.label}</legend>
              <HostsEditor
                idBase={id}
                value={isStringArray(value) ? value : []}
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
