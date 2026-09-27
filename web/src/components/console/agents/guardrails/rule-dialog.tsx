"use client";

/**
 * `rule-dialog.tsx` (V5-41, `docs/v5/PLAN-V5.md` V5-41, ask #280): add/edit
 * one guardrail rule — a `Pattern` (`RegexRule`), an `Instruction`
 * (`ClassifierRule`) or a `Moderation service` (`ProviderRule`). A dialog,
 * never a side drawer, the same `CaseDialog` precedent every other per-item
 * editor in this editor uses. Saving here only updates the section's
 * in-memory rule list (via `useFieldArray`); it reaches the api on the
 * editor's own Save, exactly like every other field this editor edits.
 *
 * Plain wording throughout (§0.1): the field labels never say "regex",
 * "classifier" or "moderation" — the kind picker below is the only place
 * those ideas appear, spelled out as "Pattern" / "Instruction" / "Moderation
 * service".
 */
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useProviders } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { MODERATION_CATEGORY_VALUES, type GuardrailRuleForm } from "@/components/console/lib/schemas";

import { CATEGORY_LABELS } from "./kinds";
import { PatternTester } from "./pattern-tester";

const KIND_OPTIONS: { value: GuardrailRuleForm["kind"]; label: string; hint: string }[] = [
  { value: "regex", label: "Pattern", hint: "Matches exact wording, e.g. a card number or a banned phrase." },
  { value: "classifier", label: "Instruction", hint: "A plain-language rule a model judges the text against." },
  { value: "provider", label: "Moderation service", hint: "A hosted service that flags unsafe content." },
];

function emptyRule(kind: GuardrailRuleForm["kind"], name: string): GuardrailRuleForm {
  switch (kind) {
    case "classifier":
      return { kind: "classifier", name, prompt: "" };
    case "provider":
      return { kind: "provider", name, provider: "openai_moderation", categories: [], credential_id: null };
    case "regex":
    default:
      return { kind: "regex", name, pattern: "", ignore_case: true };
  }
}

export interface RuleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` creates a new rule; otherwise edits a copy of it. */
  initial: GuardrailRuleForm | null;
  /** Every other rule's name in this stage, for the uniqueness check (excludes `initial`'s own name). */
  otherNames: readonly string[];
  /** What this stage checks, for the dialog's description ("what the caller says", …). */
  stageLabel: string;
  /** The api's own message for this rule (e.g. "the pattern does not compile: …"), shown on the field. */
  serverError?: string;
  onSave: (value: GuardrailRuleForm) => void;
}

export function RuleDialog({ open, onOpenChange, initial, otherNames, stageLabel, serverError, onSave }: RuleDialogProps) {
  const [draft, setDraft] = React.useState<GuardrailRuleForm>(() => initial ?? emptyRule("regex", ""));
  const [attempted, setAttempted] = React.useState(false);
  const providersQuery = useProviders();

  React.useEffect(() => {
    if (!open) return;
    setDraft(initial ?? emptyRule("regex", ""));
    setAttempted(false);
  }, [open, initial]);

  const trimmedName = draft.name.trim();
  const nameError =
    attempted && trimmedName === ""
      ? "Name is required"
      : otherNames.some((other) => other.trim().toLowerCase() === trimmedName.toLowerCase())
        ? "Two rules in this list can't share a name"
        : undefined;
  const patternError =
    draft.kind === "regex" && attempted && draft.pattern.trim() === "" ? "Pattern is required" : undefined;
  const promptError =
    draft.kind === "classifier" && attempted && draft.prompt.trim() === "" ? "Instruction is required" : undefined;
  const canSave = trimmedName !== "" && !nameError && !patternError && !promptError;

  const openaiSpec = providersQuery.data?.providers.find((provider) => provider.id === "openai-llm");

  function handleSave() {
    setAttempted(true);
    if (trimmedName === "" || nameError || patternError || promptError) return;
    onSave({ ...draft, name: trimmedName });
    onOpenChange(false);
  }

  function setKind(kind: GuardrailRuleForm["kind"]) {
    setDraft((prev) => (prev.kind === kind ? prev : emptyRule(kind, prev.name)));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{initial ? "Edit rule" : "Add a rule"}</DialogTitle>
          <DialogDescription>Checks {stageLabel}. Choose how the rule decides what trips it.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium text-foreground">How it decides</legend>
            <RadioGroup
              value={draft.kind}
              onValueChange={(value) => setKind(value as GuardrailRuleForm["kind"])}
              aria-label="How it decides"
              className="flex flex-col gap-2"
            >
              {KIND_OPTIONS.map((option) => {
                const id = `guardrail-kind-${option.value}`;
                return (
                  <label
                    key={option.value}
                    htmlFor={id}
                    className="flex items-start gap-2.5 rounded-md border border-border p-2.5 text-sm has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft/40"
                  >
                    <RadioGroupItem id={id} value={option.value} className="mt-0.5" />
                    <span className="flex flex-col gap-0.5">
                      <span className="font-medium text-foreground">{option.label}</span>
                      <span className="text-[0.8125rem] text-pretty text-muted-foreground">{option.hint}</span>
                    </span>
                  </label>
                );
              })}
            </RadioGroup>
          </fieldset>

          <Field label="Name" htmlFor="guardrail-rule-name" hint="Shown on the call timeline when it trips." error={nameError}>
            <Input
              id="guardrail-rule-name"
              value={draft.name}
              maxLength={60}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
            />
          </Field>

          {draft.kind === "regex" ? (
            <>
              <Field
                label="Pattern"
                htmlFor="guardrail-rule-pattern"
                hint="A regular expression, e.g. a run of 13–19 digits for a card number."
                error={patternError ?? serverError}
              >
                <Textarea
                  id="guardrail-rule-pattern"
                  rows={2}
                  maxLength={300}
                  className="font-mono text-sm"
                  value={draft.pattern}
                  onChange={(event) => setDraft((prev) => (prev.kind === "regex" ? { ...prev, pattern: event.target.value } : prev))}
                />
              </Field>
              <Field inline label="Match upper and lower case alike" htmlFor="guardrail-rule-ignore-case">
                <Checkbox
                  id="guardrail-rule-ignore-case"
                  checked={draft.ignore_case}
                  onCheckedChange={(checked) =>
                    setDraft((prev) => (prev.kind === "regex" ? { ...prev, ignore_case: checked === true } : prev))
                  }
                />
              </Field>
              <PatternTester pattern={draft.pattern} ignoreCase={draft.ignore_case} />
            </>
          ) : null}

          {draft.kind === "classifier" ? (
            <Field
              label="Instruction"
              htmlFor="guardrail-rule-prompt"
              hint="What the text must not do, in plain words, e.g. 'Gives medical advice: tells the caller what medicine or dose to take.'"
              error={promptError ?? serverError}
            >
              <Textarea
                id="guardrail-rule-prompt"
                rows={3}
                maxLength={1000}
                value={draft.prompt}
                onChange={(event) => setDraft((prev) => (prev.kind === "classifier" ? { ...prev, prompt: event.target.value } : prev))}
              />
            </Field>
          ) : null}

          {draft.kind === "provider" ? (
            <>
              <fieldset className="flex flex-col gap-2">
                <legend className="text-sm font-medium text-foreground">What it flags</legend>
                <p className="text-[0.8125rem] text-muted-foreground">
                  Leave every box unchecked to trip on anything the service flags.
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {MODERATION_CATEGORY_VALUES.map((category) => {
                    const id = `guardrail-category-${category}`;
                    const checked = draft.categories.includes(category);
                    return (
                      <label key={category} htmlFor={id} className="flex items-start gap-2 text-sm">
                        <Checkbox
                          id={id}
                          checked={checked}
                          onCheckedChange={(next) =>
                            setDraft((prev) =>
                              prev.kind === "provider"
                                ? {
                                    ...prev,
                                    categories:
                                      next === true
                                        ? [...prev.categories, category]
                                        : prev.categories.filter((c) => c !== category),
                                  }
                                : prev,
                            )
                          }
                        />
                        <span>{CATEGORY_LABELS[category]}</span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
              {openaiSpec ? (
                <CredentialPicker
                  spec={openaiSpec}
                  value={draft.credential_id}
                  onChange={(credentialId) =>
                    setDraft((prev) => (prev.kind === "provider" ? { ...prev, credential_id: credentialId } : prev))
                  }
                  label="OpenAI key"
                  required={false}
                  error={serverError}
                />
              ) : (
                <p className="text-[0.8125rem] text-muted-foreground">
                  No key selected uses the agent&apos;s own OpenAI key.
                </p>
              )}
            </>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={!canSave}>
            {initial ? "Save rule" : "Add rule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
