"use client";

import * as React from "react";
import { useFieldArray, useFormContext } from "react-hook-form";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Field, Icon } from "@/components/shared";
import { Section, SectionRow } from "@/components/shared/section";
import { ToolsTab } from "@/components/console/agents/tabs/tools-tab";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import type { EditorSectionProps } from "@/components/console/agents/editor/types";
import { TELEPHONY_TOOLS, TELEPHONY_TOOLS_HINT } from "@/components/console/lib/constants";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

/**
 * The agent editor's Tools section (V2-19, rulings R-V2-21 / R-V2-25): WP-5's
 * `ToolsTab`, followed by a "Phone calls" card with the two phone-only tool
 * toggles and the transfer-destinations list (`config.telephony.transfer_targets`).
 * `transfer_call` never dials free text: the model may only pick one of these,
 * and the api refuses any destination outside the workspace's dialing policy
 * when the agent is saved.
 */
export function ToolsSection({ agent }: EditorSectionProps) {
  return (
    <div className="flex flex-col gap-6">
      <ToolsTab agent={agent} />
      <PhoneCallsCard />
    </div>
  );
}

export function PhoneCallsCard() {
  const { control, register, watch, setValue, formState } = useFormContext<AgentEditorForm>();
  const { fields, append, remove } = useFieldArray({ control, name: "config.telephony.transfer_targets" });
  const builtinDisabled = watch("config.tools.builtin_disabled") ?? [];
  const { issueFor } = useSectionIssues("tools");
  const targetErrors = formState.errors.config?.telephony?.transfer_targets;
  const listId = React.useId();

  function toggle(name: string, enabled: boolean) {
    setValue(
      "config.tools.builtin_disabled",
      enabled ? builtinDisabled.filter((existing) => existing !== name) : Array.from(new Set([...builtinDisabled, name])),
      { shouldDirty: true },
    );
  }

  return (
    <Section
      id="tools-telephony"
      title="Phone calls"
      description="Only used when this agent answers or places a phone call."
    >
      {TELEPHONY_TOOLS.map((tool) => {
        const id = `telephony-tool-${tool.name}`;
        const needsTargets = tool.name === "transfer_call" && fields.length === 0;
        const reason = needsTargets ? "Add a transfer destination first" : null;
        const hint =
          tool.name === "send_dtmf"
            ? `${cap(TELEPHONY_TOOLS_HINT)}. ${tool.help} Needs keypad input turned on for the agent.`
            : `${cap(TELEPHONY_TOOLS_HINT)}. ${reason ?? tool.help}`;
        return (
          <SectionRow key={tool.name}>
            <Field inline label={tool.label} htmlFor={id} hint={hint}>
              <Switch
                id={id}
                checked={!builtinDisabled.includes(tool.name) && !needsTargets}
                disabled={needsTargets}
                onCheckedChange={(checked) => toggle(tool.name, checked)}
              />
            </Field>
          </SectionRow>
        );
      })}

      <SectionRow>
        <div className="flex flex-col gap-3">
          <div>
            <h3 id={listId} className="text-sm font-medium">
              Transfer destinations
            </h3>
            <p className="mt-0.5 max-w-[65ch] text-[0.8125rem] text-muted-foreground">
              The only people or numbers the agent may transfer a caller to. Use a number like +15551234567 or a
              sip: address; each must be allowed by the workspace&apos;s outbound dialing policy (Telephony page).
            </p>
          </div>
          {fields.length > 0 ? (
            <ul aria-labelledby={listId} className="flex flex-col gap-3">
              {fields.map((field, index) => {
                const labelId = `transfer-target-${index}-label`;
                const toId = `transfer-target-${index}-to`;
                const labelError =
                  targetErrors?.[index]?.label?.message ?? issueFor(`telephony.transfer_targets.${index}.label`)?.message;
                const toError =
                  targetErrors?.[index]?.to?.message ?? issueFor(`telephony.transfer_targets.${index}.to`)?.message;
                return (
                  <li key={field.id} className="flex flex-wrap items-start gap-2">
                    <Field label="Name" htmlFor={labelId} error={labelError} className="min-w-40 flex-1">
                      <Input
                        id={labelId}
                        placeholder="Front desk"
                        autoComplete="off"
                        data-issue-path={`telephony.transfer_targets[${index}].label`}
                        {...register(`config.telephony.transfer_targets.${index}.label`)}
                      />
                    </Field>
                    <Field label="Number or SIP address" htmlFor={toId} error={toError} className="min-w-56 flex-[2]">
                      <Input
                        id={toId}
                        placeholder="+15551234567"
                        inputMode="tel"
                        autoComplete="off"
                        spellCheck={false}
                        className="font-mono text-[0.8125rem]"
                        data-issue-path={`telephony.transfer_targets[${index}].to`}
                        {...register(`config.telephony.transfer_targets.${index}.to`)}
                      />
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="mt-6"
                      aria-label={`Remove destination ${index + 1}`}
                      onClick={() => remove(index)}
                    >
                      <Icon as={Trash2Icon} size="sm" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[0.8125rem] text-muted-foreground">
              No destinations: the agent cannot transfer calls.
            </p>
          )}
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={fields.length >= 50}
              onClick={() => append({ label: "", to: "" }, { shouldFocus: true })}
            >
              <Icon as={PlusIcon} size="sm" />
              Add destination
            </Button>
          </div>
        </div>
      </SectionRow>
    </Section>
  );
}

function cap(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
