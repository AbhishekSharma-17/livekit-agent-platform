"use client";

/**
 * "Languages" (V5-35, `docs/v5/PLAN-V5.md` V5-35): `voice.languages` (first =
 * default), `voice.auto_detect` and a voice per language
 * (`voice.voices_by_language`), mounted as a card in the Conversation
 * section (`conversation-section.tsx`). The worker reads `languages` first
 * when it is non-empty, but the single-language `voice.language` field
 * (still the Instructions tab's own "Language" select) must never disagree
 * with it — every edit here also writes `voice.language = languages[0]`
 * (ask #203(3)), so `languages-card.tsx` is the one place both need to
 * change and this file's edit stays inside its own files.
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { PencilIcon, StarIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { Switch } from "@/components/ui/switch";
import { ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { LANGUAGE_OPTIONS, languageLabel } from "@/panels/blocks/catalog";
import type { ProviderRef } from "@/contracts/lkap-contracts";

function LanguageRow({
  code,
  isDefault,
  onMakeDefault,
  onRemove,
  onSetVoice,
  voice,
}: {
  code: string;
  isDefault: boolean;
  onMakeDefault: () => void;
  onRemove: () => void;
  onSetVoice: () => void;
  voice: ProviderRef | null | undefined;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 py-1">
      <div className="flex items-center gap-2">
        <span className="font-medium text-foreground">{languageLabel(code)}</span>
        {isDefault ? <StatusChip tone="neutral">Default</StatusChip> : null}
        <span className="text-label text-text-secondary">
          {voice?.provider_id ? `Voice: ${voice.provider_id}` : "Uses the agent's own voice"}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {!isDefault ? (
          <Button type="button" variant="ghost" size="sm" onClick={onMakeDefault}>
            <Icon as={StarIcon} size="sm" /> Make default
          </Button>
        ) : null}
        <Button type="button" variant="ghost" size="sm" onClick={onSetVoice}>
          <Icon as={PencilIcon} size="sm" /> Voice
        </Button>
        <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${languageLabel(code)}`} onClick={onRemove}>
          <Icon as={Trash2Icon} size="sm" />
        </Button>
      </div>
    </div>
  );
}

export function LanguagesCard() {
  const { control, setValue } = useFormContext<AgentEditorForm>();
  const languages = useWatch({ control, name: "config.voice.languages" }) ?? [];
  const voicesByLanguage = useWatch({ control, name: "config.voice.voices_by_language" }) ?? {};
  const [pendingAdd, setPendingAdd] = React.useState("");
  const [voiceDialogFor, setVoiceDialogFor] = React.useState<string | null>(null);

  function writeLanguages(next: string[]) {
    setValue("config.voice.languages", next, { shouldDirty: true });
    // The worker reads `languages` first when it is non-empty; keep the single-language
    // field the Instructions tab still shows in lockstep so the two can never disagree
    // (ask #203(3)). An empty list falls back to whatever `voice.language` already is.
    if (next.length > 0) setValue("config.voice.language", next[0], { shouldDirty: true });
  }

  function addLanguage(code: string) {
    if (code === "" || languages.includes(code)) {
      setPendingAdd("");
      return;
    }
    writeLanguages([...languages, code]);
    setPendingAdd("");
  }

  function removeLanguage(code: string) {
    writeLanguages(languages.filter((l) => l !== code));
  }

  function makeDefault(code: string) {
    writeLanguages([code, ...languages.filter((l) => l !== code)]);
  }

  const availableToAdd = LANGUAGE_OPTIONS.filter((option) => !languages.includes(option.value));
  const canSwitchMidCall = languages.length > 1;

  return (
    <Section
      id="conversation-languages"
      title="Languages"
      description="Other languages the agent may speak besides its default. With more than one, it can switch mid-call."
    >
      <SectionRow className="flex flex-col divide-y divide-border">
        {languages.length === 0 ? (
          <p className="py-1 text-sm text-text-secondary">
            Just the agent&apos;s one language (set on the Instructions tab). Add another below to let it switch.
          </p>
        ) : (
          languages.map((code, index) => (
            <LanguageRow
              key={code}
              code={code}
              isDefault={index === 0}
              voice={voicesByLanguage[code]}
              onMakeDefault={() => makeDefault(code)}
              onRemove={() => removeLanguage(code)}
              onSetVoice={() => setVoiceDialogFor(code)}
            />
          ))
        )}
      </SectionRow>
      {availableToAdd.length > 0 ? (
        <SectionRow>
          <Field label="Add a language" htmlFor="languages-add" hint={languages.length >= 10 ? "Up to 10 languages." : undefined}>
            <SearchableSelect
              id="languages-add"
              value={pendingAdd}
              onValueChange={addLanguage}
              options={availableToAdd.map((option) => ({ value: option.value, label: option.label }))}
              placeholder="Choose a language…"
              disabled={languages.length >= 10}
              triggerClassName="w-full sm:w-64"
            />
          </Field>
        </SectionRow>
      ) : null}
      <SectionRow>
        <Field
          inline
          label="Detect the caller's language automatically"
          htmlFor="languages-auto-detect"
          hint={
            canSwitchMidCall
              ? "Follows the caller when they speak one of the languages above."
              : "Add another language above first. There's nothing to detect between yet."
          }
        >
          <Controller
            control={control}
            name="config.voice.auto_detect"
            render={({ field }) => (
              <Switch
                id="languages-auto-detect"
                checked={field.value ?? false}
                disabled={!canSwitchMidCall}
                onCheckedChange={field.onChange}
              />
            )}
          />
        </Field>
      </SectionRow>

      <Dialog open={voiceDialogFor !== null} onOpenChange={(open) => !open && setVoiceDialogFor(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{voiceDialogFor ? `Voice for ${languageLabel(voiceDialogFor)}` : "Voice"}</DialogTitle>
            <DialogDescription>Leave unset to keep the agent&apos;s own voice for this language.</DialogDescription>
          </DialogHeader>
          {voiceDialogFor ? (
            <Controller
              control={control}
              name={`config.voice.voices_by_language.${voiceDialogFor}`}
              render={({ field }) => (
                <ProviderSlotEditor
                  kind="tts"
                  value={field.value ?? null}
                  onChange={field.onChange}
                  idPrefix={`voice-${voiceDialogFor}`}
                />
              )}
            />
          ) : null}
          <DialogFooter>
            <Button variant="primary" type="button" onClick={() => setVoiceDialogFor(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Section>
  );
}
