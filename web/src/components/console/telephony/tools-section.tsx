"use client";

import * as React from "react";
import { Controller, useFieldArray, useFormContext } from "react-hook-form";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
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
 * toggles and the transfer-destinations list (`config.telephony.transfer_targets`),
 * then (V5-28) the "Send a text message" contacts list
 * (`config.telephony.sms_targets`) — usable beyond phone calls, so it is its
 * own card rather than nested under "Phone calls". `transfer_call` never
 * dials free text: the model may only pick one of these, and the api refuses
 * any destination outside the workspace's dialing policy when the agent is
 * saved.
 */
export function ToolsSection({ agent }: EditorSectionProps) {
  return (
    <div className="flex flex-col gap-6">
      <ToolsTab agent={agent} />
      <PhoneCallsCard />
      <VoicemailCard />
      <SmsContactsCard />
    </div>
  );
}

interface SelectOption {
  value: string;
  label: string;
}

/**
 * The custom Select bound to a react-hook-form `Controller` field: the
 * trigger takes the field's ref (so `setFocus` reaches it) and the
 * `data-issue-path` the editor's issue list jumps to.
 */
function FormSelect({
  id,
  field,
  options,
  issuePath,
  className,
}: {
  id: string;
  field: { name: string; value: string; onChange: (value: string) => void; onBlur: () => void; ref: React.Ref<HTMLButtonElement> };
  options: SelectOption[];
  issuePath: string;
  className?: string;
}) {
  return (
    <Select name={field.name} value={field.value} onValueChange={field.onChange}>
      <SelectTrigger
        id={id}
        ref={field.ref}
        onBlur={field.onBlur}
        data-issue-path={issuePath}
        className={className ?? "w-full"}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

const TRANSFER_MODE_OPTIONS: SelectOption[] = [
  { value: "cold", label: "Put through directly" },
  { value: "warm", label: "Introduce the caller first (LiveKit Cloud)" },
];

const ON_MACHINE_OPTIONS: SelectOption[] = [
  { value: "hangup", label: "Hang up" },
  { value: "leave_message", label: "Leave a message" },
];

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
            <h3 id={listId} className="text-body font-medium">
              Transfer destinations
            </h3>
            <p className="mt-0.5 max-w-[72ch] text-label text-text-secondary">
              The only people or numbers the agent may transfer a caller to. Use a number like +15551234567 or a
              sip: address; each must be allowed by the workspace&apos;s outbound dialing policy (Telephony page).
            </p>
          </div>
          {fields.length > 0 ? (
            <ul aria-labelledby={listId} className="flex flex-col gap-3">
              {fields.map((field, index) => {
                const labelId = `transfer-target-${index}-label`;
                const toId = `transfer-target-${index}-to`;
                const modeId = `transfer-target-${index}-mode`;
                const labelError =
                  targetErrors?.[index]?.label?.message ?? issueFor(`telephony.transfer_targets.${index}.label`)?.message;
                const toError =
                  targetErrors?.[index]?.to?.message ?? issueFor(`telephony.transfer_targets.${index}.to`)?.message;
                // V5-32/36: a `warm` target off LiveKit Cloud, or without exactly one
                // outbound line, is a *warning* (the transfer still works — it falls
                // back to a direct handover) — never painted as a field error.
                const modeIssue = issueFor(`telephony.transfer_targets.${index}.mode`);
                const modeWarning = modeIssue?.severity === "warning" ? modeIssue.message : undefined;
                return (
                  <li key={field.id} className="flex flex-col gap-1.5">
                    <div className="flex flex-wrap items-start gap-2">
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
                          className="font-mono tabular-nums"
                          data-issue-path={`telephony.transfer_targets[${index}].to`}
                          {...register(`config.telephony.transfer_targets.${index}.to`)}
                        />
                      </Field>
                      <Field label="How the call is handed over" htmlFor={modeId} className="min-w-56 flex-[2]">
                        <Controller
                          control={control}
                          name={`config.telephony.transfer_targets.${index}.mode`}
                          defaultValue="cold"
                          render={({ field: mode }) => (
                            <FormSelect
                              id={modeId}
                              field={{ ...mode, value: mode.value ?? "cold" }}
                              options={TRANSFER_MODE_OPTIONS}
                              issuePath={`telephony.transfer_targets[${index}].mode`}
                            />
                          )}
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
                    </div>
                    {modeWarning ? (
                      <p data-issue-path={`telephony.transfer_targets[${index}].mode`} className="text-label text-warning-text">
                        {modeWarning}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-label text-text-secondary">
              No destinations: the agent cannot transfer calls.
            </p>
          )}
          <div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={fields.length >= 50}
              onClick={() => append({ label: "", to: "", mode: "cold" }, { shouldFocus: true })}
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

/**
 * V5-32/V5-36 (`docs/v5/_asks.md` #211): whether the agent notices an
 * answering machine on an outbound call, and what it does about it
 * (`config.telephony.amd`, `lkap_contracts.telephony.AmdConfig`). Outbound
 * calls only — inbound calls and browser/text sessions never see this.
 */
export function VoicemailCard() {
  const { control, watch, formState } = useFormContext<AgentEditorForm>();
  const { issueFor } = useSectionIssues("tools");
  const enabled = watch("config.telephony.amd.enabled");
  const onMachine = watch("config.telephony.amd.on_machine");
  const enabledId = "amd-enabled";
  const onMachineId = "amd-on-machine";
  const messageId = "amd-message";
  const ivrId = "amd-ivr-detection";
  const messageError = formState.errors.config?.telephony?.amd?.message?.message;
  const enabledIssue = issueFor("telephony.amd.enabled");
  const messageIssue = issueFor("telephony.amd.message");
  const enabledWarning = enabledIssue?.severity === "warning" ? enabledIssue.message : undefined;
  const messageWarning = !messageError && messageIssue?.severity === "warning" ? messageIssue.message : undefined;

  return (
    <Section
      id="tools-voicemail"
      title="Voicemail"
      description="Outbound calls only: what the agent does when a machine answers instead of a person."
    >
      <SectionRow>
        <Field
          inline
          label="Detect answering machines"
          htmlFor={enabledId}
          hint="Listens for a voicemail greeting or a phone menu before the agent speaks."
        >
          <Controller
            control={control}
            name="config.telephony.amd.enabled"
            render={({ field }) => (
              <Switch
                id={enabledId}
                data-issue-path="telephony.amd.enabled"
                checked={field.value}
                onCheckedChange={field.onChange}
              />
            )}
          />
        </Field>
        {enabledWarning ? (
          <p data-issue-path="telephony.amd.enabled" className="text-label text-warning-text">
            {enabledWarning}
          </p>
        ) : null}
      </SectionRow>

      {enabled ? (
        <>
          <SectionRow>
            <Field label="When a machine answers" htmlFor={onMachineId} className="max-w-xs">
              <Controller
                control={control}
                name="config.telephony.amd.on_machine"
                render={({ field }) => (
                  <FormSelect
                    id={onMachineId}
                    field={{ ...field, value: field.value ?? "hangup" }}
                    options={ON_MACHINE_OPTIONS}
                    issuePath="telephony.amd.on_machine"
                  />
                )}
              />
            </Field>
          </SectionRow>

          <SectionRow>
            <Field
              label="Message"
              htmlFor={messageId}
              error={messageError}
              hint="What the agent says to the voicemail. Leave empty for a short call-back request."
              className={onMachine === "leave_message" ? undefined : "opacity-60"}
            >
              <Controller
                control={control}
                name="config.telephony.amd.message"
                render={({ field }) => (
                  <Textarea
                    id={messageId}
                    rows={3}
                    maxLength={1000}
                    placeholder="Please call us back at your convenience."
                    disabled={onMachine !== "leave_message"}
                    data-issue-path="telephony.amd.message"
                    value={field.value ?? ""}
                    onChange={(event) => field.onChange(event.target.value === "" ? null : event.target.value)}
                  />
                )}
              />
            </Field>
            {messageWarning ? (
              <p data-issue-path="telephony.amd.message" className="text-label text-warning-text">
                {messageWarning}
              </p>
            ) : null}
          </SectionRow>

          <SectionRow>
            <Field
              inline
              label="Let the agent work through phone menus"
              htmlFor={ivrId}
              hint="Off: a phone menu is treated the same as an answering machine and the agent hangs up."
            >
              <Controller
                control={control}
                name="config.telephony.amd.ivr_detection"
                render={({ field }) => <Switch id={ivrId} checked={field.value} onCheckedChange={field.onChange} />}
              />
            </Field>
          </SectionRow>
        </>
      ) : null}
    </Section>
  );
}

/**
 * V5-25/V5-28 (`docs/v5/_asks.md` #154): the numbers `send_sms` may text
 * besides the caller of the current phone call. The destination policy is
 * fixed (`agent/src/lkap_agent/tools/builtin/send_sms.py`): the model never
 * types a number — on a phone call it may text the caller automatically, and
 * anyone else (on a phone call, a browser chat or a text chat) must already
 * be a saved contact here, picked by name.
 */
export function SmsContactsCard() {
  const { control, register, formState } = useFormContext<AgentEditorForm>();
  const { fields, append, remove } = useFieldArray({ control, name: "config.telephony.sms_targets" });
  const { issueFor } = useSectionIssues("tools");
  const targetErrors = formState.errors.config?.telephony?.sms_targets;
  const listId = React.useId();

  return (
    <Section
      id="tools-sms-contacts"
      title="Send a text message — saved contacts"
      description="On a phone call, the agent can text the caller automatically. To let it text anyone else — on a phone call, a browser chat or a text chat — save their number here with a name; the agent can only pick from this list, never a number typed on the spot."
    >
      <SectionRow>
        <div className="flex flex-col gap-3">
          <h3 id={listId} className="sr-only">
            Saved SMS contacts
          </h3>
          {fields.length > 0 ? (
            <ul aria-labelledby={listId} className="flex flex-col gap-3">
              {fields.map((field, index) => {
                const labelId = `sms-target-${index}-label`;
                const toId = `sms-target-${index}-to`;
                const labelError =
                  targetErrors?.[index]?.label?.message ?? issueFor(`telephony.sms_targets.${index}.label`)?.message;
                const toError = targetErrors?.[index]?.to?.message ?? issueFor(`telephony.sms_targets.${index}.to`)?.message;
                return (
                  <li key={field.id} className="flex flex-wrap items-start gap-2">
                    <Field label="Name" htmlFor={labelId} error={labelError} className="min-w-40 flex-1">
                      <Input
                        id={labelId}
                        placeholder="Claims desk"
                        autoComplete="off"
                        data-issue-path={`telephony.sms_targets[${index}].label`}
                        {...register(`config.telephony.sms_targets.${index}.label`)}
                      />
                    </Field>
                    <Field label="Mobile number" htmlFor={toId} error={toError} className="min-w-56 flex-[2]">
                      <Input
                        id={toId}
                        placeholder="+15551234567"
                        inputMode="tel"
                        autoComplete="off"
                        spellCheck={false}
                        className="font-mono tabular-nums"
                        data-issue-path={`telephony.sms_targets[${index}].to`}
                        {...register(`config.telephony.sms_targets.${index}.to`)}
                      />
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="mt-6"
                      aria-label={`Remove contact ${index + 1}`}
                      onClick={() => remove(index)}
                    >
                      <Icon as={Trash2Icon} size="sm" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-label text-text-secondary">
              No saved contacts: off a phone call, the agent has no one to text.
            </p>
          )}
          <div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={fields.length >= 50}
              onClick={() => append({ label: "", to: "" }, { shouldFocus: true })}
            >
              <Icon as={PlusIcon} size="sm" />
              Add contact
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
