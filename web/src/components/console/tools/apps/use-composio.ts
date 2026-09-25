"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { StatusTone } from "@/components/shared/status-chip";
import { useAppsStatus, useTestCredential } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ApiError } from "@/lib/api";
import type { AppsStatusOut } from "@/contracts/lkap-contracts";

/**
 * The shared status for Composio's key (docs/v5/COMPOSIO.md §6, D-V5-C13):
 * Tools -> Apps' header and Console -> Keys' Composio row both read this one
 * hook, so the chip can never diverge between the two screens. "Validate"
 * here is the same `POST /v1/credentials/{id}/test` the generic row already
 * uses (a re-test of the *stored* key); `key/test` (the *pasted* key, before
 * it is ever saved) is `useTestAppsKey` in `api-hooks.ts`, used only by
 * `enable-composio-dialog.tsx`.
 */
export function useComposioStatus() {
  const queryClient = useQueryClient();
  const statusQuery = useAppsStatus();
  const testMutation = useTestCredential();
  const credentialId = statusQuery.data?.credential_id ?? null;

  const validate = React.useCallback(async () => {
    if (!credentialId) return null;
    const result = await testMutation.mutateAsync(credentialId);
    await queryClient.invalidateQueries({ queryKey: ["apps", "status"] });
    return result;
  }, [credentialId, testMutation, queryClient]);

  return {
    status: statusQuery.data,
    isLoading: statusQuery.isLoading,
    isError: statusQuery.isError,
    error: statusQuery.error,
    refetch: statusQuery.refetch,
    validate,
    validating: testMutation.isPending,
  };
}

export interface ComposioChip {
  tone: StatusTone;
  label: string;
}

/** "Valid" / "Invalid" / "Not tested" / "Not set up" — the one chip both screens render. */
export function composioStatusChip(status: AppsStatusOut | undefined): ComposioChip {
  if (!status || status.credential_id == null) return { tone: "neutral", label: "Not set up" };
  if (!status.enabled) return { tone: "neutral", label: "Turned off" };
  if (status.last_test_ok === true) return { tone: "success", label: "Valid" };
  if (status.last_test_ok === false) return { tone: "danger", label: "Invalid" };
  return { tone: "neutral", label: "Not tested" };
}

/**
 * Plain wording for the two Apps-specific error codes (docs/v5/COMPOSIO.md
 * §4); every other error falls back to `errorMessage` (the api's own
 * messages here are already plain, e.g. "add a Composio key before enabling
 * Apps" — this only renames the two codes a builder is most likely to hit
 * from a stale UI state, where the server message alone reads oddly).
 */
export function appsErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "apps_not_enabled") {
      return error.message || "Turn Apps on in Tools → Apps before doing that.";
    }
    if (error.code === "tool_provider_unauthorized") {
      return "Composio rejected the stored key. Rotate it in Tools → Apps.";
    }
  }
  return errorMessage(error);
}
