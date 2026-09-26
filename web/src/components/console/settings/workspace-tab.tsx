"use client";

import * as React from "react";
import Link from "next/link";
import { LogOutIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DescriptionList } from "@/components/shared/description-list";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useMe } from "@/components/console/shell/use-me";
import { api } from "@/lib/api";
import { ROLE_LABEL } from "./api-types";
import { useActiveWorkspace, useInvalidateSettings, useWorkspace } from "./use-settings-queries";
import { SkeletonRows } from "@/components/shared/loading-state";
import { BREAK_GLASS_USER_ID, signOut } from "@/components/console/lib/sign-out";

/** `admin`/`owner` may rename the workspace or change its settings (CONTRACTS-V2 §3.2). */
function canManageWorkspace(role: string | undefined): boolean {
  return role === "admin" || role === "owner";
}

/** `settings.locale.timezone` (R-V5-10) — the default timezone new agents are created with. */
function workspaceDefaultTimezone(settings: Record<string, unknown> | undefined): string {
  const locale = settings?.locale;
  const zone = locale && typeof locale === "object" ? (locale as { timezone?: unknown }).timezone : undefined;
  return typeof zone === "string" ? zone : "";
}

/**
 * A short curated fallback for runtimes without `Intl.supportedValuesOf`
 * (mirrors `instructions-tab.tsx`'s own copy — both V5-52 files).
 */
const FALLBACK_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Asia/Kolkata",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Australia/Sydney",
];

function supportedTimezones(): string[] {
  try {
    const withSupportedValuesOf = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    const values = withSupportedValuesOf.supportedValuesOf?.("timeZone");
    if (values && values.length > 0) return values;
  } catch {
    // fall through to the curated list
  }
  return FALLBACK_TIMEZONES;
}

export function WorkspaceTab() {
  const { workspace: membership, isLoading: meLoading } = useActiveWorkspace();
  const workspaceQuery = useWorkspace(membership?.id);
  const invalidate = useInvalidateSettings();
  const timezoneListId = React.useId();
  const timezones = React.useMemo(() => supportedTimezones(), []);

  const [name, setName] = React.useState("");
  const [timezone, setTimezone] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [initialized, setInitialized] = React.useState(false);

  const workspace = workspaceQuery.data;
  React.useEffect(() => {
    if (workspace && !initialized) {
      setName(workspace.name);
      setTimezone(workspaceDefaultTimezone(workspace.settings));
      setInitialized(true);
    }
  }, [workspace, initialized]);

  async function onSave(event: React.FormEvent) {
    event.preventDefault();
    if (!membership) return;
    setSaving(true);
    try {
      // R-V5-10: `settings.locale.timezone` is the default timezone new agents
      // are created with (an empty field clears it, so agents fall back to UTC).
      await api.put(`workspaces/${membership.id}`, { name, settings: { locale: { timezone: timezone || null } } });
      invalidate(membership.id);
      toast.success("Workspace updated");
    } catch (err) {
      toast.error(`Couldn't save workspace — ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  if (meLoading || workspaceQuery.isLoading || !membership) {
    return (
      <Section id="workspace" title="Workspace">
        <SectionRow>
          <SkeletonRows label="Loading workspace" rows={3} rowClassName="h-9" />
        </SectionRow>
      </Section>
    );
  }

  const canManage = canManageWorkspace(membership.role);

  return (
    <div className="space-y-6">
      <Section
        id="workspace"
        title="Workspace"
        description="Name and default timezone for this workspace."
        aside={
          <StatusChip tone="neutral" size="sm">
            {ROLE_LABEL[membership.role]}
          </StatusChip>
        }
      >
        <SectionRow>
          {canManage ? (
            <form onSubmit={onSave} className="grid max-w-lg gap-4 sm:grid-cols-2">
              <Field label="Name" htmlFor="workspace-name" className="sm:col-span-2">
                <Input
                  id="workspace-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  disabled={saving}
                  required
                />
              </Field>
              <Field
                label="Default timezone for new agents"
                htmlFor="workspace-timezone"
                optional
                hint="e.g. America/New_York. Leave empty to start new agents on UTC."
              >
                <Input
                  id="workspace-timezone"
                  list={timezoneListId}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono text-[0.8125rem]"
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                  placeholder="UTC"
                  disabled={saving}
                />
                <datalist id={timezoneListId}>
                  {timezones.map((tz) => (
                    <option key={tz} value={tz} />
                  ))}
                </datalist>
              </Field>
              <div className="sm:col-span-2">
                <Button type="submit" disabled={saving}>
                  Save
                </Button>
              </div>
            </form>
          ) : (
            <DescriptionList
              columns={2}
              items={[
                { term: "Name", detail: workspace?.name },
                { term: "Default timezone for new agents", detail: timezone || "UTC" },
              ]}
            />
          )}
        </SectionRow>
        <SectionRow>
          <DescriptionList
            columns={2}
            items={[
              { term: "Slug", detail: workspace?.slug, mono: true },
              {
                term: "Default connection",
                detail: (
                  <Link href="/console/connections" className="underline underline-offset-2">
                    Manage in Connections
                  </Link>
                ),
              },
            ]}
          />
        </SectionRow>
      </Section>

      <AccountSection />
    </div>
  );
}

/** The signed-in user's own account: email, and (real sessions only) password + sign out. */
function AccountSection() {
  const { me } = useMe();
  const isBreakGlass = me?.user.id === BREAK_GLASS_USER_ID;

  return (
    <Section id="account" title="Account" description="Your own sign-in.">
      <SectionRow>
        <DescriptionList
          columns={2}
          items={[
            { term: "Name", detail: me?.user.name || "—" },
            { term: "Email", detail: me?.user.email, mono: true },
          ]}
        />
      </SectionRow>
      {isBreakGlass ? (
        <SectionRow className="text-sm text-muted-foreground">
          Signed in with the break-glass admin token — there is no user session to change a password on or sign
          out of. Sign in at <code className="font-mono">/login</code> to manage a real account.
        </SectionRow>
      ) : (
        <SectionRow>
          <PasswordForm />
        </SectionRow>
      )}
      {!isBreakGlass ? (
        <SectionRow>
          <Button type="button" variant="outline" onClick={() => void signOut()}>
            <Icon as={LogOutIcon} size="sm" />
            Sign out
          </Button>
        </SectionRow>
      ) : null}
    </Section>
  );
}

function PasswordForm() {
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await api.post("auth/password", { current, new: next });
      toast.success("Password changed. Other sessions were signed out.");
      setCurrent("");
      setNext("");
    } catch (err) {
      toast.error(`Couldn't change password — ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="grid max-w-sm gap-4">
      <Field label="Current password" htmlFor="account-current-password" required>
        <Input
          id="account-current-password"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          disabled={saving}
        />
      </Field>
      <Field label="New password" htmlFor="account-new-password" required hint="At least 8 characters.">
        <Input
          id="account-new-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={next}
          onChange={(event) => setNext(event.target.value)}
          disabled={saving}
        />
      </Field>
      <div>
        <Button type="submit" disabled={saving}>
          Change password
        </Button>
      </div>
    </form>
  );
}
