"use client";

import * as React from "react";
import { useState } from "react";
import { PlusIcon, TrashIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateCredential } from "@/components/console/lib/api-hooks";
import { defaultFieldValues, RegistryForm, type FieldValues } from "@/components/console/registry/registry-form";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { CredentialOut, ProviderSpec } from "@/contracts/lkap-contracts";

interface KeyValuePair {
  name: string;
  value: string;
}

/**
 * "create-credential modal posting `CredentialCreate`, showing `fingerprint`
 * only" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE). Handles the `secret_bag` kind
 * (`http-tool-secret`, `secret_fields=[]`) with a free-form NAME=value editor
 * instead of the registry-typed fields every other provider gets — see
 * docs/IMPLEMENTATION_PLAN.md's "critical notes" on the 18th MVP provider.
 */
export function CreateCredentialDialog({
  spec,
  onCreated,
  trigger,
}: {
  spec: ProviderSpec;
  onCreated: (credential: CredentialOut) => void;
  trigger: React.ReactNode;
}) {
  const labelId = React.useId();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [fieldValues, setFieldValues] = useState<FieldValues>(() => defaultFieldValues(spec.secret_fields ?? []));
  const [pairs, setPairs] = useState<KeyValuePair[]>([{ name: "", value: "" }]);
  const mutation = useCreateCredential();

  const isFreeForm = (spec.secret_fields?.length ?? 0) === 0;

  function reset() {
    setLabel("");
    setFieldValues(defaultFieldValues(spec.secret_fields ?? []));
    setPairs([{ name: "", value: "" }]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    const secrets: Record<string, string> = isFreeForm
      ? Object.fromEntries(
          pairs.filter((pair) => pair.name.trim() !== "").map((pair) => [pair.name.trim(), pair.value]),
        )
      : Object.fromEntries(Object.entries(fieldValues).map(([key, value]) => [key, String(value)]));

    if (label.trim() === "") {
      toast.error("Label is required.");
      return;
    }
    if (Object.keys(secrets).length === 0) {
      toast.error(isFreeForm ? "Add at least one NAME=value pair." : "Fill in the required fields.");
      return;
    }

    try {
      const credential = await mutation.mutateAsync({
        provider_id: spec.id,
        label: label.trim(),
        secrets,
      });
      reset();
      setOpen(false);
      toast.success(`Credential "${credential.label}" saved.`);
      onCreated(credential);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>New {spec.label} credential</DialogTitle>
            <DialogDescription>
              Secret values are encrypted at rest and never shown again after saving.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div>
              <label htmlFor={labelId} className="mb-1 block text-sm font-medium">
                Label
              </label>
              <Input
                id={labelId}
                autoComplete="off"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder={`e.g. "Production ${spec.vendor} key"`}
              />
            </div>

            {isFreeForm ? (
              <fieldset className="m-0 border-0 p-0">
                <legend className="mb-1 block text-sm font-medium">Secrets</legend>
                <div className="space-y-2">
                  {pairs.map((pair, index) => (
                    <div key={index} className="flex items-center gap-2">
                      <Input
                        placeholder="NAME"
                        value={pair.name}
                        onChange={(event) =>
                          setPairs((prev) =>
                            prev.map((p, i) => (i === index ? { ...p, name: event.target.value.toUpperCase() } : p)),
                          )
                        }
                        className="font-mono text-xs"
                      />
                      <Input
                        type="password"
                        autoComplete="new-password"
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
                        aria-label="Remove"
                        onClick={() => setPairs((prev) => prev.filter((_, i) => i !== index))}
                      >
                        <TrashIcon className="size-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => setPairs((prev) => [...prev, { name: "", value: "" }])}
                >
                  <PlusIcon className="size-3.5" /> Add pair
                </Button>
              </fieldset>
            ) : (
              <RegistryForm
                fields={spec.secret_fields ?? []}
                values={fieldValues}
                onChange={(name, value) => setFieldValues((prev) => ({ ...prev, [name]: value }))}
                idPrefix={`cred-${spec.id}`}
              />
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Saving…" : "Save credential"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
