"use client";

import * as React from "react";
import { PlusIcon, TrashIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { CopyButton } from "@/components/shared/copy-button";
import { DescriptionList } from "@/components/shared/description-list";
import { Field } from "@/components/shared/field";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useCreateCredential, useProviders, useUpdateCredential } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { CredentialTestResultView, useCredentialTest } from "@/components/console/registry/credential-test";
import {
  KIND_LABEL,
  KIND_ORDER,
  credentialDisplay,
  isSelectableForCredentials,
  suggestedCredentialLabel,
} from "@/components/console/registry/provider-meta";
import { defaultFieldValues, RegistryForm, type FieldValues } from "@/components/console/registry/registry-form";
import type { CredentialOut, ProviderSpec } from "@/contracts/lkap-contracts";

export type CredentialDialogMode = "create" | "rotate" | "rename";

export interface CredentialDialogProps {
  /** Controlled open state; omit both and pass `trigger` for an uncontrolled dialog. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
  /** `create` (default): a new key. `rotate`: new secrets for `credential`. `rename`: label only. */
  mode?: CredentialDialogMode;
  /**
   * The provider. In `create` mode it is pre-filled and locked (opened from a
   * slot); omit it for a vendor picker (the credentials page).
   */
  spec?: ProviderSpec;
  /** The stored credential (`rotate` / `rename`). */
  credential?: CredentialOut;
  /** Called once the api has stored the key (before the dialog shows the saved state). */
  onSaved?: (credential: CredentialOut) => void;
}

interface KeyValuePair {
  name: string;
  value: string;
}

const EMPTY_PAIRS: KeyValuePair[] = [{ name: "", value: "" }];

/**
 * Credential dialog (docs/UI_UX_SPEC.md §4.5, §7.5 item 5 — a modal since
 * UI_UX_SPEC-V2-AMENDMENTS §5: no side drawers;
 * UI_UX_SPEC-V2-AMENDMENTS §3 WP-4: "Test result + last tested"). Used from
 * the slot editor's credential picker, the tool editors (through
 * `CredentialPicker`) and the credentials page.
 *
 * Secrets live in local `useState` only — never in the agent editor's
 * react-hook-form state — and are cleared the moment the api accepts them;
 * after saving the dialog shows the fingerprint and a "Test key" action, never
 * the secret. Posts `CredentialCreate` (create) or `CredentialUpdate` (rotate:
 * blank secret inputs keep the stored values, so an all-blank rotate sends
 * the label only; rename: label only).
 *
 * The submit handler stops propagation: the dialog is portalled, but React
 * events still bubble through the component tree into the agent editor's
 * `<form>`.
 */
export function CredentialDialog({
  open: openProp,
  onOpenChange,
  trigger,
  mode = "create",
  spec: specProp,
  credential,
  onSaved,
}: CredentialDialogProps) {
  const [openState, setOpenState] = React.useState(false);
  const open = openProp ?? openState;
  // Fetched whenever the provider isn't locked (the vendor picker needs the
  // full list) *and* whenever a locked `specProp` is itself an alias
  // (`credential_provider` set): only then can `credentialDisplay` below
  // enumerate every sibling kind a shared credential home covers — the
  // whole point of R-V4-7's follow-up fix (a key added from `openrouter-tts`
  // read as LLM-only because the dialog only ever saw that one alias's own
  // spec). A locked spec that is itself a credential home (e.g. opened from
  // the LLM slot) needs no extra fetch: it already reads correctly as
  // itself, `credentialDisplay` degrades to "keep today's label" without
  // the registry, and the home's own dialog intentionally says nothing
  // about sharing (there is nothing to add from behind that slot).
  const needsRegistry = !specProp || Boolean(specProp.credential_provider);
  const providersQuery = useProviders({ enabled: needsRegistry });
  const registry = React.useMemo(
    () => providersQuery.data?.providers ?? (specProp ? [specProp] : []),
    [providersQuery.data, specProp],
  );

  const [providerId, setProviderId] = React.useState<string>(specProp?.id ?? credential?.provider_id ?? "");
  const spec = specProp ?? registry.find((p) => p.id === (credential?.provider_id ?? providerId));
  const display = spec ? credentialDisplay(spec, registry) : null;

  const [label, setLabel] = React.useState("");
  const [labelTouched, setLabelTouched] = React.useState(false);
  const [fieldValues, setFieldValues] = React.useState<FieldValues>({});
  const [pairs, setPairs] = React.useState<KeyValuePair[]>(EMPTY_PAIRS);
  const [errors, setErrors] = React.useState<Record<string, string | undefined>>({});
  const [saved, setSaved] = React.useState<CredentialOut | null>(null);

  const createMutation = useCreateCredential();
  const updateMutation = useUpdateCredential();
  const pending = createMutation.isPending || updateMutation.isPending;
  const isFreeForm = spec ? (spec.secret_fields?.length ?? 0) === 0 : false;

  const reset = React.useCallback(() => {
    setProviderId(specProp?.id ?? credential?.provider_id ?? "");
    setLabel(mode === "create" ? (specProp ? suggestedCredentialLabel(specProp) : "") : (credential?.label ?? ""));
    setLabelTouched(false);
    setFieldValues(mode === "create" && specProp ? defaultFieldValues(specProp.secret_fields ?? []) : {});
    setPairs(EMPTY_PAIRS);
    setErrors({});
    setSaved(null);
  }, [credential, mode, specProp]);

  // Fresh state every time the dialog opens.
  React.useEffect(() => {
    if (open) reset();
  }, [open, reset]);

  function setOpen(next: boolean) {
    setOpenState(next);
    onOpenChange?.(next);
    if (!next) {
      // Drop any typed secret immediately; do not wait for the next open.
      setFieldValues({});
      setPairs(EMPTY_PAIRS);
    }
  }

  function chooseProvider(id: string) {
    setProviderId(id);
    const next = registry.find((p) => p.id === id);
    setFieldValues(defaultFieldValues(next?.secret_fields ?? []));
    setPairs(EMPTY_PAIRS);
    setErrors({});
    if (next && !labelTouched) setLabel(suggestedCredentialLabel(next));
  }

  function collectSecrets(): Record<string, string> {
    if (isFreeForm) {
      return Object.fromEntries(
        pairs.filter((pair) => pair.name.trim() !== "" && pair.value !== "").map((pair) => [pair.name.trim(), pair.value]),
      );
    }
    return Object.fromEntries(
      Object.entries(fieldValues)
        .map(([key, value]) => [key, String(value)] as const)
        .filter(([, value]) => value !== ""),
    );
  }

  function validate(secrets: Record<string, string>): Record<string, string | undefined> {
    const next: Record<string, string | undefined> = {};
    if (label.trim() === "") next.label = "Give the key a name you'll recognise.";
    if (mode === "create") {
      if (!spec) next.provider = "Choose a provider.";
      else if (isFreeForm) {
        if (Object.keys(secrets).length === 0) next.pairs = "Add at least one NAME and value.";
      } else {
        for (const field of spec.secret_fields ?? []) {
          if (field.required && !secrets[field.name]) next[field.name] = `Enter the ${field.label.toLowerCase()}.`;
        }
      }
    }
    return next;
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (saved) {
      setOpen(false);
      return;
    }

    const secrets = mode === "rename" ? {} : collectSecrets();
    const nextErrors = validate(secrets);
    setErrors(nextErrors);
    const firstInvalid = Object.keys(nextErrors).find((key) => nextErrors[key]);
    if (firstInvalid) {
      const targetId =
        firstInvalid === "label"
          ? "credential-dialog-label"
          : firstInvalid === "provider"
            ? "credential-dialog-provider"
            : firstInvalid === "pairs"
              ? "credential-dialog-pair-0-name"
              : `credential-dialog-${firstInvalid}`;
      document.getElementById(targetId)?.focus();
      return;
    }

    try {
      let result: CredentialOut;
      if (mode === "create") {
        result = await createMutation.mutateAsync({ provider_id: spec!.id, label: label.trim(), secrets });
      } else {
        const hasSecrets = Object.keys(secrets).length > 0;
        result = await updateMutation.mutateAsync({
          id: credential!.id,
          body: hasSecrets ? { label: label.trim(), secrets } : { label: label.trim() },
        });
      }
      // The api has the secret now; the browser does not need it any more.
      setFieldValues({});
      setPairs(EMPTY_PAIRS);
      onSaved?.(result);
      if (mode === "rename") {
        toast.success("Renamed");
        setOpen(false);
        return;
      }
      toast.success(mode === "create" ? "Credential added" : "Key rotated");
      setSaved(result);
    } catch (error) {
      toast.error(`Couldn't save — ${errorMessage(error)}`);
    }
  }

  const title = saved
    ? "Key saved"
    : mode === "rotate"
      ? `Rotate ${credential?.label ?? "key"}`
      : mode === "rename"
        ? `Rename ${credential?.label ?? "key"}`
        : spec && specProp
          ? // "Add OpenRouter key", not "Add OpenRouter (TTS) key" (R-V4-7's
            // follow-up): opened from any OpenRouter slot — LLM, STT, TTS,
            // embeddings or image generation — this should read the same,
            // since the key it saves works for all of them.
            `Add ${display!.title} key`
          : "Add credential";

  const pickable = React.useMemo(
    () =>
      registry
        .filter((p) => p.requires_credential !== false && isSelectableForCredentials(p))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [registry],
  );

  const content = (
    <DialogContent size="md" aria-describedby="credential-dialog-description">
      <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription id="credential-dialog-description">
            Encrypted at rest. Only a fingerprint is shown after saving.
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {saved ? (
            <SavedView credential={saved} title={display?.title ?? spec?.label} />
          ) : (
            <>
              {mode === "create" && !specProp ? (
                <Field label="Provider" htmlFor="credential-dialog-provider" required error={errors.provider}>
                  <Select value={providerId || undefined} onValueChange={chooseProvider}>
                    <SelectTrigger id="credential-dialog-provider" className="w-full">
                      <SelectValue placeholder={providersQuery.isLoading ? "Loading providers…" : "Choose a provider"} />
                    </SelectTrigger>
                    <SelectContent>
                      {KIND_ORDER.map((kind) => {
                        const items = pickable.filter((p) => p.kind === kind);
                        if (items.length === 0) return null;
                        return (
                          <SelectGroup key={kind}>
                            <SelectLabel>{KIND_LABEL[kind]}</SelectLabel>
                            {items.map((p) => (
                              <SelectItem key={p.id} value={p.id}>
                                {p.label}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        );
                      })}
                    </SelectContent>
                  </Select>
                </Field>
              ) : spec ? (
                <div className="flex items-center gap-2.5">
                  <VendorMark vendor={spec.vendor} size="md" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{display!.title}</p>
                    <p className="text-xs text-muted-foreground">{KIND_LABEL[spec.kind]}</p>
                  </div>
                </div>
              ) : null}

              {display && display.usedBy.length > 1 ? (
                <p className="text-[0.8125rem] text-pretty text-muted-foreground">
                  One key for all {display.title} services: {display.usedBy.join(", ")}.
                </p>
              ) : null}

              <Field label="Name" htmlFor="credential-dialog-label" required error={errors.label} hint="Shown in pickers next to the fingerprint.">
                <Input
                  id="credential-dialog-label"
                  autoComplete="off"
                  value={label}
                  onChange={(event) => {
                    setLabel(event.target.value);
                    setLabelTouched(true);
                  }}
                  placeholder={spec ? suggestedCredentialLabel(spec) : "Production key"}
                />
              </Field>

              {mode !== "rename" && spec ? (
                isFreeForm ? (
                  <SecretPairs pairs={pairs} setPairs={setPairs} error={errors.pairs} masked={mode === "rotate"} />
                ) : (
                  <RegistryForm
                    fields={spec.secret_fields ?? []}
                    values={fieldValues}
                    onChange={(name, value) => setFieldValues((prev) => ({ ...prev, [name]: value }))}
                    secretsMasked={mode === "rotate"}
                    idPrefix="credential-dialog"
                    errors={errors}
                    singleColumn
                  />
                )
              ) : null}

              {spec?.get_key_url && mode === "create" ? (
                <p className="text-[0.8125rem] text-muted-foreground">
                  Need a key?{" "}
                  <a
                    href={spec.get_key_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-foreground underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Get one from {spec.vendor}
                  </a>
                </p>
              ) : null}

              {mode !== "create" && credential ? <CurrentKey credential={credential} /> : null}
            </>
          )}
        </DialogBody>

        <DialogFooter>
          {saved ? (
            <Button type="submit">Done</Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? "Saving…" : mode === "rename" ? "Save name" : "Save key"}
              </Button>
            </>
          )}
        </DialogFooter>
      </form>
    </DialogContent>
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      {content}
    </Dialog>
  );
}

/** After save: what was stored (never the secret) + "Test key". */
function SavedView({ credential, title }: { credential: CredentialOut; title: string | undefined }) {
  return (
    <>
      <DescriptionList
        columns={1}
        items={[
          { term: "Provider", detail: title ?? credential.provider_id },
          { term: "Name", detail: credential.label },
          {
            term: "Fingerprint",
            detail: (
              <span className="inline-flex items-center gap-1">
                <span className="font-mono text-[0.8125rem] tabular-nums">{credential.fingerprint}</span>
                <CopyButton value={credential.fingerprint} label="Copy fingerprint" size="xs" />
              </span>
            ),
          },
        ]}
      />
      <TestKeyBlock credentialId={credential.id} />
    </>
  );
}

/** Rotate / rename: the key currently stored, with its test state. */
function CurrentKey({ credential }: { credential: CredentialOut }) {
  return (
    <div className="flex flex-col gap-3 border-t border-border pt-5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">Current key</p>
          <p className="font-mono text-[0.8125rem] text-muted-foreground tabular-nums">{credential.fingerprint}</p>
        </div>
      </div>
      <TestKeyBlock credentialId={credential.id} />
    </div>
  );
}

function TestKeyBlock({ credentialId }: { credentialId: string }) {
  const { last, run, pending } = useCredentialTest(credentialId);
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => void run()} disabled={pending}>
          {pending ? "Testing…" : last ? "Test again" : "Test key"}
        </Button>
      </div>
      <CredentialTestResultView record={last} pending={pending} />
    </div>
  );
}

/** `secret_bag` providers (`http-tool-secret`): free-form NAME/value pairs. */
function SecretPairs({
  pairs,
  setPairs,
  error,
  masked,
}: {
  pairs: KeyValuePair[];
  setPairs: React.Dispatch<React.SetStateAction<KeyValuePair[]>>;
  error?: string;
  masked: boolean;
}) {
  return (
    <fieldset className="m-0 flex flex-col gap-2 border-0 p-0" aria-describedby="credential-dialog-pairs-hint">
      <legend className="mb-1.5 text-sm font-medium text-foreground">Secrets</legend>
      <p id="credential-dialog-pairs-hint" className="-mt-1 text-[0.8125rem] text-muted-foreground">
        {masked
          ? "Pairs you add replace the stored ones. Leave empty to keep them."
          : "Reference them in tool headers, URLs and bodies as {{ secret.NAME }}."}
      </p>
      {pairs.map((pair, index) => (
        <div key={index} className="flex items-center gap-2">
          <Input
            id={`credential-dialog-pair-${index}-name`}
            aria-label={`Secret ${index + 1} name`}
            placeholder="NAME"
            value={pair.name}
            onChange={(event) =>
              setPairs((prev) => prev.map((p, i) => (i === index ? { ...p, name: event.target.value.toUpperCase() } : p)))
            }
            className="font-mono text-xs"
          />
          <Input
            type="password"
            autoComplete="new-password"
            aria-label={`Secret ${index + 1} value`}
            placeholder="value"
            value={pair.value}
            onChange={(event) =>
              setPairs((prev) => prev.map((p, i) => (i === index ? { ...p, value: event.target.value } : p)))
            }
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove secret ${index + 1}`}
            onClick={() => setPairs((prev) => (prev.length === 1 ? EMPTY_PAIRS : prev.filter((_, i) => i !== index)))}
          >
            <TrashIcon aria-hidden="true" />
          </Button>
        </div>
      ))}
      {error ? <p className="text-[0.8125rem] text-danger-text">{error}</p> : null}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => setPairs((prev) => [...prev, { name: "", value: "" }])}>
          <PlusIcon aria-hidden="true" /> Add pair
        </Button>
      </div>
    </fieldset>
  );
}
