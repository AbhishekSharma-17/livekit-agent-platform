"use client";

/**
 * `qa-section.tsx` (V5-34, `docs/v5/PLAN-V5.md` V5-34): the post-call fields
 * editor — what a judge LLM fills in from the finished conversation once QA
 * scoring runs (`config.qa.fields`). Mounted as a card inside the "Privacy"
 * section (`privacy-section.tsx`) alongside the Privacy card; there's no
 * console editor for `config.qa.enabled`/`rubric_prompt`/`model` yet, so the
 * card says so in one line rather than pretending fields run on their own.
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Input } from "@/components/ui/input";
import { Section, SectionRow } from "@/components/shared/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type {
  AgentEditorForm,
  QaFieldForm,
} from "@/components/console/lib/schemas";

const FIELD_TYPES: { value: QaFieldForm["type"]; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / no" },
  { value: "select", label: "One of a list" },
];

/**
 * `options` is a plain `string[]`, not an array of objects — `useFieldArray`
 * needs object items (it merges its own `id` onto each one), so this array
 * is edited as one `Controller`-bound value instead (`field.value`/`onChange`
 * over the whole list), the same shape `case-dialog.tsx`'s expectations use
 * locally, here bound to the form.
 */
function OptionsEditor({ index }: { index: number }) {
  const { control } = useFormContext<AgentEditorForm>();
  return (
    <Controller
      control={control}
      name={`config.qa.fields.${index}.options`}
      render={({ field }) => {
        const options = field.value ?? [];
        return (
          <div className="flex flex-col gap-1.5">
            <span className="text-label font-medium">Choices</span>
            {options.map((option, optionIndex) => (
              <div key={optionIndex} className="flex items-center gap-2">
                <Input
                  aria-label={`Choice ${optionIndex + 1}`}
                  value={option}
                  maxLength={100}
                  onChange={(event) =>
                    field.onChange(
                      options.map((o, i) =>
                        i === optionIndex ? event.target.value : o,
                      ),
                    )
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Remove choice"
                  onClick={() =>
                    field.onChange(options.filter((_, i) => i !== optionIndex))
                  }
                >
                  <Icon as={Trash2Icon} size="sm" />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              onClick={() => field.onChange([...options, ""])}
            >
              <Icon as={PlusIcon} size="sm" /> Add a choice
            </Button>
          </div>
        );
      }}
    />
  );
}

function FieldRow({
  index,
  onRemove,
}: {
  index: number;
  onRemove: () => void;
}) {
  const { control } = useFormContext<AgentEditorForm>();
  const type = useWatch({ control, name: `config.qa.fields.${index}.type` });
  const baseId = `qa-field-${index}`;
  return (
    <div className="flex flex-col gap-3 py-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_10rem_auto]">
        <Controller
          control={control}
          name={`config.qa.fields.${index}.name`}
          render={({ field, fieldState }) => (
            <Field
              label="Field name"
              htmlFor={`${baseId}-name`}
              error={fieldState.error?.message}
            >
              <Input
                id={`${baseId}-name`}
                className="font-mono text-sm"
                value={field.value}
                onChange={field.onChange}
                maxLength={48}
              />
            </Field>
          )}
        />
        <Controller
          control={control}
          name={`config.qa.fields.${index}.type`}
          render={({ field }) => (
            <Field label="Type" htmlFor={`${baseId}-type`}>
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id={`${baseId}-type`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FIELD_TYPES.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Remove field"
          className="self-end"
          onClick={onRemove}
        >
          <Icon as={Trash2Icon} size="sm" />
        </Button>
      </div>
      <Controller
        control={control}
        name={`config.qa.fields.${index}.description`}
        render={({ field }) => (
          <Field
            label="What it means"
            htmlFor={`${baseId}-description`}
            optional
            hint="Shown to the judge so it fills this in correctly."
          >
            <Textarea
              id={`${baseId}-description`}
              rows={2}
              maxLength={500}
              value={field.value}
              onChange={field.onChange}
            />
          </Field>
        )}
      />
      {type === "select" ? <OptionsEditor index={index} /> : null}
    </div>
  );
}

function emptyField(): QaFieldForm {
  return { name: "", type: "text", options: [], description: "" };
}

export function QaFieldsCard() {
  const { control } = useFormContext<AgentEditorForm>();
  const qaEnabled = useWatch({ control, name: "config.qa.enabled" });

  return (
    <Controller
      control={control}
      name="config.qa.fields"
      render={({ field }) => {
        const rows = field.value ?? [];
        return (
          <Section
            id="privacy-qa-fields"
            title="Post-call fields"
            description="Facts a judge model fills in from the finished conversation, once post-call scoring runs (e.g. the kind of claim, or whether an appointment was booked)."
            aside={
              <Button
                type="button"
                size="sm"
                onClick={() => field.onChange([...rows, emptyField()])}
                disabled={rows.length >= 20}
              >
                <Icon as={PlusIcon} size="sm" /> Add field
              </Button>
            }
          >
            {qaEnabled === false ? (
              <SectionRow>
                <p className="text-label text-warning-text">
                  These run only when post-call scoring is turned on for this
                  agent.
                </p>
              </SectionRow>
            ) : null}
            <SectionRow className="flex flex-col divide-y divide-border">
              {rows.length === 0 ? (
                <p className="py-1 text-sm text-text-secondary">
                  No fields yet.
                </p>
              ) : (
                rows.map((_, index) => (
                  <FieldRow
                    key={index}
                    index={index}
                    onRemove={() =>
                      field.onChange(rows.filter((_r, i) => i !== index))
                    }
                  />
                ))
              )}
            </SectionRow>
          </Section>
        );
      }}
    />
  );
}
