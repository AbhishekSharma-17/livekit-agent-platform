"use client";

import * as React from "react";
import { PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field, fieldIds } from "@/components/shared/field";
import { useCredentials, useCredentialsAcrossHomes } from "@/components/console/lib/api-hooks";
import { CredentialDialog } from "@/components/console/registry/credential-dialog";
import { CredentialTestResultView, useCredentialTest } from "@/components/console/registry/credential-test";
import { credentialDisplay } from "@/components/console/registry/provider-meta";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { CredentialOut, ProviderSpec } from "@/contracts/lkap-contracts";

const NONE = "__none__";

export interface CredentialPickerProps {
  spec: ProviderSpec;
  value: string | null | undefined;
  onChange: (credentialId: string | null) => void;
  /** Control id (defaults to a generated one). */
  id?: string;
  /** Field label (default "Key"). */
  label?: string;
  /** Marks the field "Required" (default true). */
  required?: boolean;
  /** Field-level error, e.g. from validation. */
  error?: string;
}

/**
 * Credential step of a provider slot (docs/UI_UX_SPEC.md §4.4 step 3
 * "Credential"): a `Select` of this provider's stored keys (label ·
 * fingerprint) and "Add key", which opens the credential dialog with the
 * provider locked. A key saved there is selected here. The selected key has
 * a "Test key" action with the result inline.
 *
 * Also used by the HTTP/MCP tool editors (`http-tool-secret`); keep the
 * `{ spec, value, onChange }` signature stable.
 */
export function CredentialPicker({
  spec,
  value,
  onChange,
  id,
  label = "Key",
  required = true,
  error,
}: CredentialPickerProps) {
  const autoId = React.useId();
  const selectId = id ?? `credential-${autoId}`;
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const { data, isLoading, isError, error: loadError, refetch } = useCredentials(spec.id);
  const items = data?.items ?? [];
  const selected = items.find((item) => item.id === value);
  // `registry` is left at its default ([]): this component only has its own
  // `spec`, not the full provider list, but `credentialDisplay` still
  // resolves an aliased spec (e.g. `openrouter-tts`) to the vendor title
  // from its own `vendor` field (R-V4-7's follow-up) — a key stored under a
  // shared credential home should read "OpenRouter", not "OpenRouter (TTS)",
  // everywhere, including this empty-state hint.
  const { title: credentialTitle } = credentialDisplay(spec);

  let hint: React.ReactNode;
  if (isError) {
    hint = (
      <>
        Couldn&apos;t load keys. {errorMessage(loadError)}{" "}
        <button
          type="button"
          className="rounded-sm font-medium text-foreground underline underline-offset-3"
          onClick={() => refetch()}
        >
          Try again
        </button>
      </>
    );
  } else if (!isLoading && items.length === 0) {
    hint = `No ${credentialTitle} keys yet. Add one to use this provider.`;
  }

  return (
    <div className="flex flex-col gap-2">
      <Field label={label} htmlFor={selectId} required={required} hint={hint} error={error}>
        <div className="flex items-center gap-2">
          <Select value={value ?? NONE} onValueChange={(next) => onChange(next === NONE ? null : next)} disabled={isLoading}>
            <SelectTrigger
              id={selectId}
              className="w-full min-w-0 flex-1"
              aria-invalid={error ? true : undefined}
              aria-describedby={
                [required || hint ? fieldIds(selectId).hint : null, error ? fieldIds(selectId).error : null]
                  .filter(Boolean)
                  .join(" ") || undefined
              }
            >
              <SelectValue placeholder={isLoading ? "Loading keys…" : "Choose a key"} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>No key selected</SelectItem>
              {items.map((credential) => (
                <SelectItem key={credential.id} value={credential.id}>
                  {credential.label} · <span className="font-mono">{credential.fingerprint}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="button" onClick={() => setDialogOpen(true)} className="shrink-0">
            <PlusIcon aria-hidden="true" />
            Add key
          </Button>
        </div>
      </Field>
      {selected ? <SelectedKeyTest credentialId={selected.id} /> : null}
      <CredentialDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        spec={spec}
        onSaved={(credential) => {
          onChange(credential.id);
          // A credential saved under an aliased provider (R-V4-7, e.g. a
          // key added from the `openrouter-stt` slot is stored under
          // `openrouter-llm`) lands in this list's query for `spec.id`
          // (the alias), which the dialog's own mutation cache invalidation
          // doesn't reach (it invalidates the home's key and "all", not
          // every alias). Refetch so the new key shows up selected instead
          // of vanishing until something else happens to refetch it.
          void refetch();
        }}
      />
    </div>
  );
}

function SelectedKeyTest({ credentialId }: { credentialId: string }) {
  const { last, run, pending } = useCredentialTest(credentialId);
  return (
    <div className="flex flex-col gap-1.5">
      <div>
        <Button type="button" variant="link" size="sm" className="h-auto px-0" onClick={() => void run()} disabled={pending}>
          {pending ? "Testing…" : "Test key"}
        </Button>
      </div>
      {pending || last ? <CredentialTestResultView record={last} pending={pending} /> : null}
    </div>
  );
}

export interface MultiHomeCredentialPickerProps {
  /**
   * Every registry id whose stored keys are all acceptable here — e.g. the
   * caller's own `useProviders()` result filtered through
   * `provider-meta.ts`'s `isOpenAiKeyHome` (ask #297).
   */
  providerIds: readonly string[];
  /** One `ProviderSpec` per id in `providerIds` (order doesn't matter), for "Add key" and each key's home label. */
  specs: readonly ProviderSpec[];
  value: string | null | undefined;
  onChange: (credentialId: string | null) => void;
  id?: string;
  label?: string;
  required?: boolean;
  error?: string;
}

/**
 * A `CredentialPicker` that lists keys from *several* credential homes at
 * once (ask #297) — a Moderation service rule's OpenAI key, which the api
 * accepts from any registry entry `isOpenAiKeyHome` names, not only
 * `openai-llm`. Reads every home with `useCredentialsAcrossHomes` and shows
 * each key's own home next to its label ("My prod key · OpenAI Whisper") so
 * two keys of the same name in different homes stay distinguishable. Not
 * OpenAI-specific itself — `providerIds`/`specs` are the caller's own list,
 * so this also serves any future "any key of this vendor" picker.
 *
 * "Add key" always saves the new key under `providerIds[0]`'s home (the
 * caller decides the order — `rule-dialog.tsx` puts `openai-llm` first, the
 * same single home `CredentialPicker` always used before this) — since the
 * api resolves the rule's own credential by id, not by which home it
 * happened to save under, a fresh key from here always works; choosing the
 * *matching* home for a brand-new key is a smaller, separate nicety
 * (flagged as an ask).
 */
export function MultiHomeCredentialPicker({
  providerIds,
  specs,
  value,
  onChange,
  id,
  label = "Key",
  required = true,
  error,
}: MultiHomeCredentialPickerProps) {
  const autoId = React.useId();
  const selectId = id ?? `credential-${autoId}`;
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const queries = useCredentialsAcrossHomes(providerIds);
  const specById = React.useMemo(() => new Map(specs.map((spec) => [spec.id, spec])), [specs]);
  const addKeySpec = specById.get(providerIds[0] ?? "") ?? specs[0];

  const isLoading = queries.some((query) => query.isLoading);
  const failedEvery = queries.length > 0 && queries.every((query) => query.isError);
  const items: CredentialOut[] = queries.flatMap((query) => query.data?.items ?? []);
  const selected = items.find((item) => item.id === value);

  function homeLabel(providerId: string): string {
    return specById.get(providerId)?.label ?? providerId;
  }

  let hint: React.ReactNode;
  if (failedEvery) {
    hint = (
      <>
        Couldn&apos;t load keys.{" "}
        <button
          type="button"
          className="rounded-sm font-medium text-foreground underline underline-offset-3"
          onClick={() => queries.forEach((query) => void query.refetch())}
        >
          Try again
        </button>
      </>
    );
  } else if (!isLoading && items.length === 0) {
    hint = "No OpenAI keys yet. Add one to use this provider.";
  }

  return (
    <div className="flex flex-col gap-2">
      <Field label={label} htmlFor={selectId} required={required} hint={hint} error={error}>
        <div className="flex items-center gap-2">
          <Select value={value ?? NONE} onValueChange={(next) => onChange(next === NONE ? null : next)} disabled={isLoading}>
            <SelectTrigger
              id={selectId}
              className="w-full min-w-0 flex-1"
              aria-invalid={error ? true : undefined}
              aria-describedby={
                [required || hint ? fieldIds(selectId).hint : null, error ? fieldIds(selectId).error : null]
                  .filter(Boolean)
                  .join(" ") || undefined
              }
            >
              <SelectValue placeholder={isLoading ? "Loading keys…" : "Choose a key"} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>No key selected</SelectItem>
              {items.map((credential) => (
                <SelectItem key={credential.id} value={credential.id}>
                  {credential.label} · {homeLabel(credential.provider_id)} ·{" "}
                  <span className="font-mono">{credential.fingerprint}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {addKeySpec ? (
            <Button type="button" onClick={() => setDialogOpen(true)} className="shrink-0">
              <PlusIcon aria-hidden="true" />
              Add key
            </Button>
          ) : null}
        </div>
      </Field>
      {selected ? <SelectedKeyTest credentialId={selected.id} /> : null}
      {addKeySpec ? (
        <CredentialDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          spec={addKeySpec}
          onSaved={(credential) => {
            onChange(credential.id);
            queries.forEach((query) => void query.refetch());
          }}
        />
      ) : null}
    </div>
  );
}
