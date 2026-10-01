"use client";

import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useIdentifyConnection } from "@/components/console/lib/api-hooks";
import { appsErrorToast } from "@/components/console/tools/apps/use-composio";
import { cn } from "@/lib/utils";
import type { AppConnectionOut } from "@/contracts/lkap-contracts";

/**
 * Who a connected app account is signed in as (V6-35, docs/v5/COMPOSIO.md §2):
 * the address, user name or workspace name the app itself reports, so two
 * accounts of one app can be told apart everywhere an account is listed. The
 * helpers here are the one place that decides how an account is named; every
 * row, card and picker reads them.
 */

type AccountLike = Pick<AppConnectionOut, "label" | "identity" | "toolkit"> &
  Partial<Pick<AppConnectionOut, "toolkit_name">>;

/** Shown where an account has no identity yet (and next to "Check now"). */
export const UNIDENTIFIED_ACCOUNT = "Account not identified yet";

/** The account's identity, trimmed, or `null` while it is not known. */
export function accountIdentity(account: Pick<AppConnectionOut, "identity">): string | null {
  const value = account.identity?.trim();
  return value ? value : null;
}

/** The account's own name: its label, else the app's name. */
export function accountLabel(account: AccountLike, appName?: string): string {
  return account.label?.trim() || appName || account.toolkit_name || account.toolkit;
}

/**
 * One line naming the account: "Work · sam@example.com", or just the identity
 * when the label already is it (an account named after its address), or just
 * the label while the identity is unknown.
 */
export function accountName(account: AccountLike, appName?: string): string {
  const label = accountLabel(account, appName);
  const identity = accountIdentity(account);
  if (!identity || identity.toLowerCase() === label.toLowerCase()) return label;
  return `${label} · ${identity}`;
}

/**
 * The account with its app, for a picker that lists accounts of several apps:
 * "Gmail · Work · sam@example.com". The label is left out when it only repeats
 * the app's name.
 */
export function appAccountName(account: AccountLike): string {
  const app = account.toolkit_name || account.toolkit;
  const label = account.label?.trim();
  const identity = accountIdentity(account);
  const parts = [app];
  if (label && label.toLowerCase() !== app.toLowerCase()) parts.push(label);
  if (identity && identity.toLowerCase() !== (label ?? "").toLowerCase()) parts.push(identity);
  return parts.join(" · ");
}

/**
 * The secondary line under an account's name: its identity when the label
 * doesn't already say it, or "Account not identified yet" with a "Check now"
 * button that asks the app again (hidden with `checkable={false}`, e.g. for a
 * sign-in still in progress).
 */
export function AccountIdentityLine({
  connection,
  checkable = true,
  className,
}: {
  connection: Pick<AppConnectionOut, "id" | "label" | "identity">;
  checkable?: boolean;
  className?: string;
}) {
  const identifyMutation = useIdentifyConnection();
  const identity = accountIdentity(connection);

  if (identity) {
    if (identity.toLowerCase() === (connection.label ?? "").trim().toLowerCase()) return null;
    return (
      <span className={cn("min-w-0 truncate text-caption text-text-secondary", className)} title={identity}>
        {identity}
      </span>
    );
  }

  async function checkNow() {
    try {
      const result = await identifyMutation.mutateAsync(connection.id);
      if (!accountIdentity(result)) {
        toast.info("Still not identified", { description: "The app didn't say who this account is. Try again later." });
      }
    } catch (error) {
      appsErrorToast("identify this account", error);
    }
  }

  return (
    <span className={cn("flex flex-wrap items-center gap-1.5 text-caption text-text-secondary", className)}>
      <span>{UNIDENTIFIED_ACCOUNT}</span>
      {checkable ? (
        <Button
          type="button"
          variant="link-neutral"
          size="sm"
          className="h-auto p-0 text-caption"
          busy={identifyMutation.isPending}
          busyLabel="Checking…"
          onClick={() => void checkNow()}
        >
          Check now
        </Button>
      ) : null}
    </span>
  );
}
