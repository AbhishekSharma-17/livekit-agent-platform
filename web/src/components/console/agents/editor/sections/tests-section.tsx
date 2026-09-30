"use client";

/**
 * "Tests" (V5-33, `docs/v5/PLAN-V5.md` V5-33): the agent's simulated-caller
 * test cases, running them, the run history and per-case verdicts, and the
 * publish gate. Cases live in `config.tests` and are edited a whole case at
 * a time through `CaseDialog` (a dialog, never a side drawer); running posts
 * the *saved* config version, so Run is disabled while the form is dirty.
 */
import * as React from "react";
import { Controller, useFieldArray, useFormContext, useFormState, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Switch } from "@/components/ui/switch";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { useAgentTestRun, useAgentTestRuns, useRunAgentTests } from "@/components/console/lib/api-hooks";
import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import { errorMessage } from "@/components/console/shared/error-banner";
import { pluralize } from "@/lib/format";
import type { AgentEditorForm, AgentTestForm } from "@/components/console/lib/schemas";

import { CaseDialog } from "@/components/console/agents/tests/case-dialog";
import { RunTable } from "@/components/console/agents/tests/run-table";
import { VerdictDialog } from "@/components/console/agents/tests/verdict-dialog";

function CaseRow({ testCase, onEdit, onRemove }: { testCase: AgentTestForm; onEdit: () => void; onRemove: () => void }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 py-1">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground">{testCase.name || testCase.id}</p>
        <p className="truncate text-label text-text-secondary">
          {testCase.scenario || testCase.persona_instructions}
        </p>
        {testCase.expectations.length > 0 ? (
          <p className="text-xs text-text-secondary">{pluralize(testCase.expectations.length, "expectation", "expectations")}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button type="button" variant="ghost" size="icon" aria-label={`Edit ${testCase.name || "case"}`} onClick={onEdit}>
          <Icon as={PencilIcon} size="sm" />
        </Button>
        <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${testCase.name || "case"}`} onClick={onRemove}>
          <Icon as={Trash2Icon} size="sm" />
        </Button>
      </div>
    </div>
  );
}

export function TestsSection() {
  const { control } = useFormContext<AgentEditorForm>();
  const { isDirty } = useFormState({ control });
  const ctx = useEditorContext();
  const agentId = ctx?.agent.id ?? "";

  // `useFieldArray`'s own `fields` injects/overwrites an `id` key of its own on every item
  // (react-hook-form's documented behaviour) — since `AgentTestForm` already has an `id`
  // (the case id), `fields[i]` cannot be trusted for it. `fields` is used only for its
  // stable React key and the structural ops (`append`/`update`/`remove`); the real values
  // (including each case's own id) come from `useWatch`.
  const { fields, append, update, remove } = useFieldArray({ control, name: "config.tests" });
  const testValues = useWatch({ control, name: "config.tests" }) ?? [];

  const [dialogCase, setDialogCase] = React.useState<{ index: number | null } | null>(null);
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);
  const [verdictCaseId, setVerdictCaseId] = React.useState<string | null>(null);

  const runsQuery = useAgentTestRuns(agentId);
  const selectedRun = useAgentTestRun(agentId, selectedRunId);
  const runTests = useRunAgentTests(agentId);

  const running = runTests.isPending || selectedRun.data?.status === "queued" || selectedRun.data?.status === "running";

  async function handleRun() {
    try {
      const run = await runTests.mutateAsync(undefined);
      setSelectedRunId(run.id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const editingCase = dialogCase && dialogCase.index !== null ? (testValues[dialogCase.index] ?? null) : null;
  const otherIds = testValues.filter((_, i) => i !== dialogCase?.index).map((t) => t?.id ?? "");

  const verdict = (selectedRun.data?.verdicts ?? []).find((v) => v.case_id === verdictCaseId) ?? null;

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="tests-cases"
        title="Test cases"
        description="Simulated callers the agent plays against, each with a goal and what must be true when it's done."
        aside={
          <Button type="button" size="sm" onClick={() => setDialogCase({ index: null })} disabled={fields.length >= 50}>
            <Icon as={PlusIcon} size="sm" /> Add case
          </Button>
        }
      >
        <SectionRow className="flex flex-col divide-y divide-border">
          {fields.length === 0 ? (
            <p className="py-1 text-sm text-text-secondary">No cases yet. Add one to start testing this agent.</p>
          ) : (
            fields.map((field, index) => {
              const testCase = testValues[index];
              if (!testCase) return null;
              return (
                <CaseRow
                  key={field.id}
                  testCase={testCase}
                  onEdit={() => setDialogCase({ index })}
                  onRemove={() => remove(index)}
                />
              );
            })
          )}
        </SectionRow>
      </Section>

      <Section
        id="tests-run"
        title="Run"
        description={
          ctx
            ? "Plays every case in a scratch text session and scores the transcript."
            : "Save the agent to run its tests."
        }
        aside={
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => void handleRun()}
            disabled={!ctx || fields.length === 0 || isDirty}
            busy={running}
            busyLabel="Running…"
          >
            <Icon as={PlayIcon} size="sm" /> Run
          </Button>
        }
      >
        {isDirty && fields.length > 0 ? (
          <SectionRow>
            <p className="text-label text-warning-text">
              Save your changes first — a run always plays the last saved version of this agent.
            </p>
          </SectionRow>
        ) : null}
        <SectionRow className="flex flex-col gap-3">
          <RunTable runs={runsQuery.data?.items ?? []} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />
          {selectedRun.data && (selectedRun.data.verdicts ?? []).length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {(selectedRun.data.verdicts ?? []).map((v) => (
                <button
                  key={v.case_id}
                  type="button"
                  onClick={() => setVerdictCaseId(v.case_id)}
                  className="rounded border border-border px-2 py-1 text-label hover:bg-muted"
                >
                  {v.case_name}: {v.status}
                </button>
              ))}
            </div>
          ) : null}
        </SectionRow>
      </Section>

      <Section
        id="tests-publish-gate"
        title="Publish gate"
        description="Refuse to publish this agent unless its tests pass on the version being published."
      >
        <SectionRow>
          <Field inline label="Require passing tests before publish" htmlFor="publish-gate-require">
            <Controller
              control={control}
              name="config.publish_gate.require_tests"
              render={({ field }) => (
                <Switch id="publish-gate-require" checked={field.value ?? false} onCheckedChange={field.onChange} />
              )}
            />
          </Field>
        </SectionRow>
        <SectionRow>
          <Controller
            control={control}
            name="config.publish_gate.min_pass_ratio"
            render={({ field }) => (
              <Field label="Cases that must pass" htmlFor="publish-gate-ratio" hint="100% means every case.">
                <InputGroup className="max-w-40">
                  <InputGroupInput
                    id="publish-gate-ratio"
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round((field.value ?? 1) * 100)}
                    onChange={(event) => {
                      const pct = Number(event.target.value);
                      field.onChange(Number.isFinite(pct) ? Math.min(1, Math.max(0, pct / 100)) : field.value);
                    }}
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>%</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
        </SectionRow>
      </Section>

      <CaseDialog
        open={dialogCase !== null}
        onOpenChange={(open) => !open && setDialogCase(null)}
        initial={editingCase ? { ...editingCase } : null}
        otherIds={otherIds}
        onSave={(value) => {
          if (dialogCase?.index != null) update(dialogCase.index, value);
          else append(value);
        }}
      />
      <VerdictDialog open={verdictCaseId !== null} onOpenChange={(open) => !open && setVerdictCaseId(null)} verdict={verdict} />
    </div>
  );
}
