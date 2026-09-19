"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { CreateCredentialDialog } from "@/components/console/registry/create-credential-dialog";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { ProviderSpec } from "@/contracts/lkap-contracts";

const NONE = "__none__";

export function CredentialPicker({
  spec,
  value,
  onChange,
}: {
  spec: ProviderSpec;
  value: string | null | undefined;
  onChange: (credentialId: string | null) => void;
}) {
  const selectId = React.useId();
  const { data, isLoading, isError, error, refetch } = useCredentials(spec.id);
  const items = data?.items ?? [];

  return (
    <div>
      <label htmlFor={selectId} className="mb-1 block text-sm font-medium">
        Credential<span className="ml-0.5 text-destructive">*</span>
      </label>
      <div className="flex items-center gap-2">
        <Select
          value={value ?? NONE}
          onValueChange={(next) => onChange(next === NONE ? null : next)}
          disabled={isLoading}
        >
          <SelectTrigger id={selectId} className="w-full flex-1">
            <SelectValue placeholder="Choose a credential" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>None selected</SelectItem>
            {items.map((credential) => (
              <SelectItem key={credential.id} value={credential.id}>
                {credential.label} · {credential.fingerprint}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <CreateCredentialDialog
          spec={spec}
          onCreated={(credential) => onChange(credential.id)}
          trigger={
            <Button type="button" variant="outline" size="sm">
              + New
            </Button>
          }
        />
      </div>
      {isError ? (
        <p className="mt-1 text-xs text-destructive">
          {errorMessage(error)}{" "}
          <button
            type="button"
            className="underline outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 rounded-xs"
            onClick={() => refetch()}
          >
            retry
          </button>
        </p>
      ) : null}
      {!isLoading && !isError && items.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          No {spec.label} credentials yet — create one to use this provider.
        </p>
      ) : null}
    </div>
  );
}
