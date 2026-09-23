"use client";

import * as React from "react";
import { PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field, fieldIds } from "@/components/shared/field";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { CredentialSheet } from "@/components/console/registry/credential-sheet";
import { CredentialTestResultView, useCredentialTest } from "@/components/console/registry/credential-test";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { ProviderSpec } from "@/contracts/lkap-contracts";

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
 * fingerprint) and "Add key", which opens the credential sheet with the
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
  const [sheetOpen, setSheetOpen] = React.useState(false);
  const { data, isLoading, isError, error: loadError, refetch } = useCredentials(spec.id);
  const items = data?.items ?? [];
  const selected = items.find((item) => item.id === value);

  let hint: React.ReactNode;
  if (isError) {
    hint = (
      <>
        Couldn&apos;t load keys — {errorMessage(loadError)}.{" "}
        <button
          type="button"
          className="rounded-xs font-medium text-foreground underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => refetch()}
        >
          Try again
        </button>
      </>
    );
  } else if (!isLoading && items.length === 0) {
    hint = `No ${spec.label} keys yet. Add one to use this provider.`;
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
          <Button type="button" variant="outline" onClick={() => setSheetOpen(true)} className="shrink-0">
            <PlusIcon aria-hidden="true" />
            Add key
          </Button>
        </div>
      </Field>
      {selected ? <SelectedKeyTest credentialId={selected.id} /> : null}
      <CredentialSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        spec={spec}
        onSaved={(credential) => onChange(credential.id)}
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
