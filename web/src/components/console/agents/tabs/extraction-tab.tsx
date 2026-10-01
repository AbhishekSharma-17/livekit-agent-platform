"use client";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15, D-V6-24): the "Extraction" section of the agent
 * editor — off by default. Fields reuse the flow variables' name/type/required/options
 * shape (`flow/variables-dialog.tsx`) plus a label, a hint for the extraction model,
 * "never store this" and where the value shows on the panel; a trigger picker (plain
 * words, ask #74: "Capture details", "When the agent asks" for manual); a "still needed"
 * checklist toggle for required fields; a privacy note naming the agent's own storage
 * tier. No jargon reaches the DOM ("extraction", "regex", "var." never appear in a
 * visible string; the section label itself and its nav icon are the only place the word
 * "Extraction" shows, matching the nav's existing plain-noun labels).
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { MessageCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { setTestChatOpenAgent } from "@/components/console/agents/test-chat/store";
import type {
  AgentEditorForm,
  ExtractionConfigForm,
  ExtractionFieldForm,
  ExtractionTriggerForm,
} from "@/components/console/lib/schemas";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AgentOut } from "@/contracts/lkap-contracts";

const FIELD_TYPES: { value: NonNullable<ExtractionFieldForm["type"]>; label: string }[] = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / no" },
  { value: "enum", label: "One of a list" },
  { value: "date", label: "Date" },
  { value: "phone", label: "Phone number" },
  { value: "email", label: "Email address" },
];

function emptyField(taken: readonly string[]): ExtractionFieldForm {
  let name = "detail";
  for (let n = 1; taken.includes(name); n += 1) name = `detail_${n}`;
  return {
    name,
    type: "string",
    description: "",
    required: false,
    options: null,
    label: "",
    hint: "",
    sensitive: false,
    show_in: null,
  };
}

interface PanelBlockLite {
  id: string;
  type: string;
  title: string | null;
  config: Record<string, unknown>;
}

/** A notebook block's `config.sections`, read loosely — the shape `block-config-form.tsx`'s own editor writes. */
function notebookSections(config: Record<string, unknown>): { id: string; kind: string; title: string }[] {
  const raw = config.sections;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((section): section is Record<string, unknown> => typeof section === "object" && section !== null)
    .map((section) => ({
      id: typeof section.id === "string" ? section.id : "",
      kind: typeof section.kind === "string" ? section.kind : "text",
      title: typeof section.title === "string" && section.title ? section.title : "",
    }))
    .filter((section) => section.id);
}

/** Where a field can show, built from the agent's own panel — never typed by hand. */
function showInOptions(blocks: readonly PanelBlockLite[]): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [];
  for (const block of blocks) {
    const name = block.title || block.id;
    if (block.type === "details") {
      options.push({ value: `details:${block.id}`, label: `Details · ${name}` });
    } else if (block.type === "notebook") {
      for (const section of notebookSections(block.config)) {
        if (section.kind !== "details" && section.kind !== "text") continue;
        options.push({ value: `notebook:${block.id}.${section.id}`, label: `${name} · ${section.title || section.id}` });
      }
    }
  }
  return options;
}

/**
 * `field.value` is `ExtractionConfigForm | undefined` (the zod schema is `.optional()`,
 * for a fixture built before V6-15); `toFormValues` always fills a concrete value from
 * `DEFAULT_EXTRACTION`, but that constant is typed against the *generated* `ExtractionConfig`
 * (whose `triggers[].kind` is optional — the guardrails-rule quirk noted throughout this
 * package), so it isn't itself assignable to the form's concrete-`kind` type. This literal
 * fallback matters only to the type checker.
 */
const FALLBACK_EXTRACTION: ExtractionConfigForm = {
  enabled: false,
  fields: [],
  triggers: [{ kind: "every_n_turns", n: 1 }],
  min_turn_chars: 12,
  still_needed: null,
};

function triggerOf<K extends ExtractionTriggerForm["kind"]>(
  triggers: readonly ExtractionTriggerForm[],
  kind: K,
): Extract<ExtractionTriggerForm, { kind: K }> | undefined {
  return triggers.find((trigger): trigger is Extract<ExtractionTriggerForm, { kind: K }> => trigger.kind === kind);
}

function withTrigger(
  triggers: readonly ExtractionTriggerForm[],
  next: ExtractionTriggerForm | null,
  kind: ExtractionTriggerForm["kind"],
): ExtractionTriggerForm[] {
  const rest = triggers.filter((trigger) => trigger.kind !== kind);
  return next ? [...rest, next] : rest;
}

export function ExtractionTab({ agent }: { agent: AgentOut }) {
  const { control } = useFormContext<AgentEditorForm>();
  const mode = useWatch({ control, name: "mode" });
  const storageTier = useWatch({ control, name: "config.privacy.storage_tier" });
  const blocks = (useWatch({ control, name: "config.panel.blocks" }) ?? []) as PanelBlockLite[];
  const flowNodes = useWatch({ control, name: "config.flow.nodes" }) ?? [];
  const toolIds = useWatch({ control, name: "config.tools.tool_ids" }) ?? [];
  const hasChecklist = blocks.some((block) => block.type === "checklist");
  const options = showInOptions(blocks);

  return (
    <Controller
      control={control}
      name="config.extraction"
      render={({ field }) => {
        const extraction = field.value ?? FALLBACK_EXTRACTION;
        const set = (patch: Partial<typeof extraction>) => field.onChange({ ...extraction, ...patch });
        const names = extraction.fields.map((f) => f.name);

        return (
          <div className="flex flex-col gap-4">
            <Section
              id="extraction-capture"
              title="Capture details"
              description="Facts the agent quietly pulls out of the conversation as it talks (a policy number, an estimate, whether something is urgent) without asking for them directly."
              aside={
                <label className="flex items-center gap-2 text-sm font-medium">
                  <Switch
                    checked={extraction.enabled}
                    onCheckedChange={(checked) => set({ enabled: checked })}
                    aria-label="Capture details from the conversation"
                  />
                  {extraction.enabled ? "On" : "Off"}
                </label>
              }
            >
              <SectionRow className="flex flex-col divide-y divide-border">
                {extraction.fields.length === 0 ? (
                  <p className="py-1 text-sm text-text-secondary">No fields yet.</p>
                ) : (
                  extraction.fields.map((row, index) => {
                    const base = `extraction-field-${index}`;
                    const update = (patch: Partial<ExtractionFieldForm>) =>
                      set({ fields: extraction.fields.map((f, i) => (i === index ? { ...f, ...patch } : f)) });
                    return (
                      <div key={index} className="flex flex-col gap-3 py-3" data-extraction-field={row.name}>
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_9rem_auto]">
                          <Field label="Name" htmlFor={`${base}-name`} hint="Used as {{ this }} elsewhere, lower case.">
                            <Input
                              id={`${base}-name`}
                              className="font-mono text-sm"
                              value={row.name}
                              maxLength={64}
                              onChange={(event) => update({ name: event.target.value.trim() })}
                            />
                          </Field>
                          <Field label="What to call it" htmlFor={`${base}-label`} optional>
                            <Input
                              id={`${base}-label`}
                              placeholder={row.name || "Label"}
                              value={row.label}
                              maxLength={80}
                              onChange={(event) => update({ label: event.target.value })}
                            />
                          </Field>
                          <Field label="Type" htmlFor={`${base}-type`}>
                            <Select value={row.type} onValueChange={(next) => update({ type: next as ExtractionFieldForm["type"] })}>
                              <SelectTrigger id={`${base}-type`} className="w-full">
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
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`Remove ${row.label || row.name}`}
                            className="self-end"
                            onClick={() => set({ fields: extraction.fields.filter((_f, i) => i !== index) })}
                          >
                            <Icon as={Trash2Icon} size="sm" />
                          </Button>
                        </div>
                        {row.type === "enum" ? (
                          <Field label="Options" htmlFor={`${base}-options`} hint="Comma-separated">
                            <Input
                              id={`${base}-options`}
                              value={(row.options ?? []).join(", ")}
                              onChange={(event) =>
                                update({
                                  options: event.target.value
                                    .split(",")
                                    .map((item) => item.trim())
                                    .filter(Boolean),
                                })
                              }
                            />
                          </Field>
                        ) : null}
                        <Field
                          label="Guidance for the model"
                          htmlFor={`${base}-hint`}
                          optional
                          hint='What it looks like, e.g. "the 8-character policy number, like PX-12345".'
                        >
                          <Input
                            id={`${base}-hint`}
                            value={row.hint}
                            maxLength={300}
                            onChange={(event) => update({ hint: event.target.value })}
                          />
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <Field label="Where it shows" htmlFor={`${base}-show-in`} optional>
                            <Select
                              value={row.show_in ?? "__none__"}
                              onValueChange={(next) => update({ show_in: next === "__none__" ? null : next })}
                            >
                              <SelectTrigger id={`${base}-show-in`} className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="__none__">Nowhere on the panel</SelectItem>
                                {options.map((option) => (
                                  <SelectItem key={option.value} value={option.value}>
                                    {option.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </Field>
                          <div className="flex items-end gap-4 pb-1.5">
                            <label className="flex items-center gap-2 text-sm">
                              <Checkbox checked={row.required} onCheckedChange={(checked) => update({ required: checked === true })} />
                              Required
                            </label>
                            <label className="flex items-center gap-2 text-sm">
                              <Checkbox checked={row.sensitive} onCheckedChange={(checked) => update({ sensitive: checked === true })} />
                              Never store this
                            </label>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </SectionRow>
              <SectionRow>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={extraction.fields.length >= 30}
                  onClick={() => set({ fields: [...extraction.fields, emptyField(names)] })}
                >
                  <Icon as={PlusIcon} size="sm" /> Add a field
                </Button>
              </SectionRow>
            </Section>

            <Section
              id="extraction-when"
              title="When to capture"
              description="What makes the agent check the conversation for these details."
            >
              <SectionRow className="flex flex-col gap-3">
                <label className="flex items-start gap-2 text-sm">
                  <Checkbox
                    checked={Boolean(triggerOf(extraction.triggers, "every_n_turns"))}
                    onCheckedChange={(checked) =>
                      set({
                        triggers: withTrigger(
                          extraction.triggers,
                          checked === true ? { kind: "every_n_turns", n: 1 } : null,
                          "every_n_turns",
                        ),
                      })
                    }
                  />
                  <span className="flex flex-1 flex-wrap items-center gap-2">
                    After every
                    <Input
                      type="number"
                      min={1}
                      max={20}
                      disabled={!triggerOf(extraction.triggers, "every_n_turns")}
                      className="w-16"
                      aria-label="Number of caller turns"
                      value={triggerOf(extraction.triggers, "every_n_turns")?.n ?? 1}
                      onChange={(event) => {
                        const n = Math.min(20, Math.max(1, Number(event.target.value) || 1));
                        set({ triggers: withTrigger(extraction.triggers, { kind: "every_n_turns", n }, "every_n_turns") });
                      }}
                    />
                    caller turn(s) (1 = every turn)
                  </span>
                </label>
                {toolIds.length > 0 ? (
                  <div>
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={Boolean(triggerOf(extraction.triggers, "tool"))}
                        onCheckedChange={(checked) =>
                          set({
                            triggers: withTrigger(
                              extraction.triggers,
                              checked === true ? { kind: "tool", tools: [...toolIds].slice(0, 1) } : null,
                              "tool",
                            ),
                          })
                        }
                      />
                      When one of these tools finishes
                    </label>
                    {triggerOf(extraction.triggers, "tool") ? (
                      <div className="mt-1.5 ml-6 flex flex-wrap gap-3">
                        {toolIds.map((toolId: string) => (
                          <label key={toolId} className="flex items-center gap-1.5 text-xs">
                            <Checkbox
                              checked={(triggerOf(extraction.triggers, "tool")?.tools ?? []).includes(toolId)}
                              onCheckedChange={(checked) => {
                                const current = triggerOf(extraction.triggers, "tool")?.tools ?? [];
                                const tools = checked === true ? [...current, toolId] : current.filter((id) => id !== toolId);
                                set({ triggers: withTrigger(extraction.triggers, { kind: "tool", tools }, "tool") });
                              }}
                            />
                            {toolId}
                          </label>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {mode === "flow" && flowNodes.length > 0 ? (
                  <div>
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={Boolean(triggerOf(extraction.triggers, "node_exit"))}
                        onCheckedChange={(checked) =>
                          set({ triggers: withTrigger(extraction.triggers, checked === true ? { kind: "node_exit", nodes: [] } : null, "node_exit") })
                        }
                      />
                      When a step ends (leave every step unchecked for &ldquo;any step&rdquo;)
                    </label>
                    {triggerOf(extraction.triggers, "node_exit") ? (
                      <div className="mt-1.5 ml-6 flex flex-wrap gap-3">
                        {flowNodes.map((node: { id: string; label?: string }) => (
                          <label key={node.id} className="flex items-center gap-1.5 text-xs">
                            <Checkbox
                              checked={(triggerOf(extraction.triggers, "node_exit")?.nodes ?? []).includes(node.id)}
                              onCheckedChange={(checked) => {
                                const current = triggerOf(extraction.triggers, "node_exit")?.nodes ?? [];
                                const nodes = checked === true ? [...current, node.id] : current.filter((id) => id !== node.id);
                                set({ triggers: withTrigger(extraction.triggers, { kind: "node_exit", nodes }, "node_exit") });
                              }}
                            />
                            {node.label || node.id}
                          </label>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={Boolean(triggerOf(extraction.triggers, "manual"))}
                    onCheckedChange={(checked) =>
                      set({ triggers: withTrigger(extraction.triggers, checked === true ? { kind: "manual" } : null, "manual") })
                    }
                  />
                  When the agent asks
                </label>
                {triggerOf(extraction.triggers, "manual") ? (
                  <p className="ml-6 text-xs text-text-secondary">
                    Try it in the test chat, then ask the agent to check what it has captured so far.
                  </p>
                ) : null}
              </SectionRow>
              <SectionRow className="flex flex-wrap items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={extraction.still_needed === "checklist"}
                    onCheckedChange={(checked) => set({ still_needed: checked ? "checklist" : null })}
                    aria-label="List required details not yet captured on the checklist"
                  />
                  List required details not yet captured on the checklist
                </label>
                {extraction.still_needed === "checklist" && !hasChecklist ? (
                  <p className="text-xs text-warning-text">Add a checklist block to the panel for this to show.</p>
                ) : null}
              </SectionRow>
              <SectionRow>
                <Button type="button" variant="secondary" size="sm" onClick={() => setTestChatOpenAgent(agent.id)}>
                  <Icon as={MessageCircleIcon} size="sm" /> Try it in the test chat
                </Button>
              </SectionRow>
            </Section>

            <p className="px-1 text-label text-text-secondary">
              Captured values are kept the same way the rest of this call is,{" "}
              {storageTier === "full"
                ? "in full, since this agent keeps whole conversations."
                : storageTier === "basic"
                  ? "only briefly, since this agent keeps very little."
                  : "with the same redaction as the rest of the call."}{" "}
              A field marked &ldquo;Never store this&rdquo; is left out of the record entirely, whatever the storage setting.
            </p>
          </div>
        );
      }}
    />
  );
}
