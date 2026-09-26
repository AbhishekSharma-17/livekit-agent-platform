"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/shared/copy-button";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useMcpOauthStatus, useRevokeMcpOauth, useStartMcpOauth } from "@/components/console/lib/api-hooks";
import type { McpOAuthAuth, McpOauthStatusOut } from "@/contracts/lkap-contracts";

/** `McpOAuthAuth.kind` is always `"oauth"` — the two console-facing modes are its `registration`. */
export type McpOauthUiMode = "oauth" | "own_oauth";

/** Plain-language chip for a tool row (no "DCR"/"PKCE"/"CIMD" — docs/v5/PLAN-V5.md V5-21 acceptance). */
export function mcpAuthChip(auth: McpAuthLike): { label: string; tone: StatusTone } {
  switch (auth.kind) {
    case "header":
      return { label: "Header", tone: "neutral" };
    case "oauth":
      return { label: "Sign in", tone: "info" };
    default:
      return { label: "No auth", tone: "neutral" };
  }
}

/** The subset of `McpAuth` a chip needs — avoids importing the full union where only `kind` matters. */
export interface McpAuthLike {
  kind?: "none" | "header" | "oauth";
}

/** `GET oauth/status` chip, independent of whether the tool dialog is open (tool-row.tsx). */
export function McpOauthStatusChip({ status }: { status: McpOauthStatusOut["status"] | undefined }) {
  switch (status) {
    case "connected":
      return <StatusChip tone="success">Connected</StatusChip>;
    case "needs_reauth":
      return <StatusChip tone="warning">Needs sign-in again</StatusChip>;
    case "not_connected":
    case "revoked":
    case undefined:
      return <StatusChip tone="neutral">Not connected</StatusChip>;
  }
}

/**
 * Connect / Re-connect / Disconnect for one MCP server's sign-in (V5-21; V5-16's
 * `worker_supported: true`, `needs_reauth`, `oauth/revoke`; docs/v5/_asks.md #141). Only
 * meaningful for a **saved** tool — `oauth/start` reads the stored definition, so a new,
 * unsaved server (or one with unsaved edits) has nothing to sign in to yet.
 *
 * R-V5-14 (`PLAN-V5.md` §7): the admin's own browser must navigate to the vendor, never a
 * copyable link — `window.open` (falling back to `location.assign` when popups are
 * blocked), never rendering `authorization_url` as text.
 */
export function McpOauthStatusPanel({
  toolId,
  registration,
  clientIdSet,
  dirty,
}: {
  /** `null` before the tool has been saved once. */
  toolId: string | null;
  registration: McpOauthUiMode;
  /** `false` when `registration === "own_oauth"` and no client id has been entered yet. */
  clientIdSet: boolean;
  /** The draft differs from the saved tool — signing in now would sign in the *old* server. */
  dirty: boolean;
}) {
  const [clientSecret, setClientSecret] = React.useState("");
  const [waiting, setWaiting] = React.useState(false);
  const [registerAt, setRegisterAt] = React.useState<{ redirectUri: string; issuer: string | null } | null>(null);
  const secretId = React.useId();

  const queryClient = useQueryClient();
  const statusQuery = useMcpOauthStatus(toolId, { poll: waiting });
  const start = useStartMcpOauth();
  const revoke = useRevokeMcpOauth();

  const status = statusQuery.data?.status;
  React.useEffect(() => {
    if (waiting && (status === "connected" || status === "needs_reauth")) {
      setWaiting(false);
      // The callback just bound a fresh `mcp-oauth` credential to this tool's
      // `auth.credential_id` (or flipped it to needs_reauth); refresh the tool list so a
      // Save right after Connect doesn't post the pre-connect (null or stale) id back.
      void queryClient.invalidateQueries({ queryKey: ["tools"] });
    }
  }, [waiting, status, queryClient]);

  if (toolId === null) {
    return (
      <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
        Save the server first, then sign in.
      </p>
    );
  }

  async function handleSignIn() {
    setRegisterAt(null);
    try {
      const result = await start.mutateAsync({
        id: toolId as string,
        body: clientSecret.trim() ? { client_secret: clientSecret.trim() } : undefined,
      });
      setClientSecret("");
      if (result.status === "needs_client_registration") {
        setRegisterAt({ redirectUri: result.redirect_uri, issuer: result.issuer ?? null });
        return;
      }
      const url = result.authorization_url;
      if (!url) return;
      // R-V5-14: same browser, never a link to copy. `noopener` in the features string
      // makes `window.open` return `null` even when the popup *did* open (the HTML spec:
      // a `noopener` window has no handle back to the opener) — passing it here would make
      // every sign-in also navigate this tab away via the `!popup` fallback below. Instead,
      // take the handle and sever `opener` on it directly (the same effect as `noopener`,
      // without losing the return value).
      const popup = window.open(url, "_blank");
      if (popup) {
        popup.opener = null;
        setWaiting(true);
      } else {
        // Popup blocked: still the admin's own browser, per R-V5-14 — never a copyable link.
        window.location.assign(url);
      }
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function handleDisconnect() {
    try {
      await revoke.mutateAsync(toolId as string);
      toast.success("Disconnected.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const disabledReason = dirty
    ? "Save your changes first."
    : registration === "own_oauth" && !clientIdSet
      ? "Set a client id above and save first."
      : undefined;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <McpOauthStatusChip status={status} />
        {statusQuery.data?.expires_at && status === "connected" ? (
          <span className="text-xs text-muted-foreground">
            expires <RelativeTime iso={statusQuery.data.expires_at} />
          </span>
        ) : null}
        {statusQuery.data?.issuer ? <span className="text-xs text-muted-foreground">{statusQuery.data.issuer}</span> : null}
      </div>

      {registration === "own_oauth" ? (
        <Field
          label="Client secret"
          htmlFor={secretId}
          optional
          hint="Only needed the first time you connect, or after rotating it at the vendor."
        >
          <Input
            id={secretId}
            type="password"
            autoComplete="off"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </Field>
      ) : null}

      {registerAt ? (
        <div className="flex flex-col gap-1.5 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning-text">
          <p>
            {registerAt.issuer ?? "This server"} needs an OAuth app registered first. Give it this return address,
            then paste the client ID above and try again.
          </p>
          <div className="flex items-center gap-1.5">
            <code className="min-w-0 flex-1 truncate rounded-xs bg-background/60 px-1.5 py-0.5 font-mono">
              {registerAt.redirectUri}
            </code>
            <CopyButton value={registerAt.redirectUri} label="Copy the return address" size="xs" />
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {status === "connected" || status === "needs_reauth" ? (
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleSignIn()}
              disabled={start.isPending || Boolean(disabledReason)}
              title={disabledReason}
            >
              {start.isPending ? "Starting…" : status === "needs_reauth" ? "Sign in again" : "Re-connect"}
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" variant="ghost" size="sm">
                  Disconnect
                </Button>
              }
              title="Disconnect this sign-in?"
              description="The agent can no longer call this server until an admin signs in again."
              confirmLabel="Disconnect"
              onConfirm={handleDisconnect}
            />
          </>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleSignIn()}
            disabled={start.isPending || Boolean(disabledReason)}
            title={disabledReason}
          >
            {start.isPending ? "Starting…" : "Sign in"}
          </Button>
        )}
        {waiting ? (
          <span className="text-xs text-muted-foreground">Waiting for you to finish signing in…</span>
        ) : null}
      </div>
    </div>
  );
}

/** Derives the panel's `registration` prop from a draft/definition's auth. */
export function registrationOf(auth: McpOAuthAuth | undefined): McpOauthUiMode {
  return auth?.registration === "preregistered" ? "own_oauth" : "oauth";
}

/**
 * `?oauth=ok|error` on `/console/tools` — the sign-in callback's redirect
 * (`GET /v1/oauth/mcp/callback`, `api/src/lkap_api/mcp_oauth/callback.py::console_redirect`).
 * That redirect carries **only** `oauth=ok|error`, no reason, no tool id (never a token) —
 * mirrors `ToolsPageTabs`' own `?connect=ok|error` toast for the Composio callback.
 * Mounted once, inside the page's `Suspense` boundary (`useSearchParams`).
 */
export function McpOauthReturnToast() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const oauth = searchParams.get("oauth");

  React.useEffect(() => {
    if (oauth !== "ok" && oauth !== "error") return;
    if (oauth === "ok") toast.success("Signed in.");
    else toast.error("Couldn't sign in. Try again.");
    void queryClient.invalidateQueries({ queryKey: ["tools"] });
    const next = new URLSearchParams(searchParams.toString());
    next.delete("oauth");
    const query = next.toString();
    router.replace(`/console/tools${query ? `?${query}` : ""}`, { scroll: false });
    // Runs once per `oauth` value; `router`/`queryClient`/`searchParams` are stable enough here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oauth]);

  return null;
}
