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
import { PasswordInput } from "@/components/shared/password-input";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { Section, SectionRow } from "@/components/shared/section";
import { Tag } from "@/components/shared/tag";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { useMe } from "@/components/console/shell/use-me";
import { BREAK_GLASS_USER_ID, signOut } from "@/components/console/lib/sign-out";
import { api } from "@/lib/api";
import { ROLE_LABEL } from "./api-types";
import { FormSkeleton, SettingsCardFooter } from "./settings-card";
import { useActiveWorkspace, useInvalidateSettings, useWorkspace } from "./use-settings-queries";
import { EMPTY_VALUE } from "@/lib/format";

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
  return (
    <div className="flex flex-col gap-6">
      <WorkspaceSection />
      <AccountSection />
    </div>
  );
}

/**
 * The workspace card: name and default timezone. Admins and owners edit and
 * save it here; everyone else gets the same facts read-only, with a note that
 * names who can change them.
 */
function WorkspaceSection() {
  const { workspace: membership, isLoading: meLoading } = useActiveWorkspace();
  const workspaceQuery = useWorkspace(membership?.id);
  const invalidate = useInvalidateSettings();
  const timezoneListId = React.useId();
  const timezones = React.useMemo(() => supportedTimezones(), []);

  const [name, setName] = React.useState("");
  const [timezone, setTimezone] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<unknown>(null);
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
    setSaveError(null);
    try {
      // R-V5-10: `settings.locale.timezone` is the default timezone new agents
      // are created with (an empty field clears it, so agents fall back to UTC).
      await api.put(`workspaces/${membership.id}`, { name, settings: { locale: { timezone: timezone || null } } });
      invalidate(membership.id);
      toast.success("Workspace saved");
    } catch (err) {
      // Keep what was typed; say what happened in the card.
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  const canManage = canManageWorkspace(membership?.role);
  const loading = meLoading || workspaceQuery.isLoading;

  if (loading) {
    return (
      <Section id="workspace" title="Workspace" description="Name and default timezone for this workspace.">
        <SectionRow>
          <FormSkeleton label="Loading workspace" fields={2} columns={2} />
        </SectionRow>
      </Section>
    );
  }

  if (workspaceQuery.isError || !membership || !workspace) {
    return (
      <Section id="workspace" title="Workspace" description="Name and default timezone for this workspace.">
        <SectionRow>
          <ErrorBanner
            error={workspaceQuery.error ?? new Error("This workspace isn't available.")}
            context={{ action: "load the workspace" }}
            onRetry={() => void workspaceQuery.refetch()}
          />
        </SectionRow>
      </Section>
    );
  }

  const facts = (
    <DescriptionList
      columns={2}
      items={[
        { term: "Slug", detail: workspace.slug, mono: true },
        {
          term: "Default connection",
          detail: (
            <Button asChild variant="link">
              <Link href="/console/connections">Manage in Connections</Link>
            </Button>
          ),
        },
      ]}
    />
  );

  return (
    <Section
      id="workspace"
      title="Workspace"
      description="Name and default timezone for this workspace."
      aside={<Tag>Your role: {ROLE_LABEL[membership.role]}</Tag>}
    >
      {canManage ? (
        <form onSubmit={onSave} className="flex flex-col divide-y divide-border">
          <SectionRow className="flex flex-col gap-4">
            {saveError ? <ErrorBanner error={saveError} context={{ action: "save the workspace" }} /> : null}
            <div className="grid max-w-lg gap-4 sm:grid-cols-2">
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
                hint="For example America/New_York. Leave empty to start new agents on UTC."
              >
                <Input
                  id="workspace-timezone"
                  list={timezoneListId}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono"
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                  placeholder="UTC"
                  disabled={saving}
                />
              </Field>
              <datalist id={timezoneListId}>
                {timezones.map((tz) => (
                  <option key={tz} value={tz} />
                ))}
              </datalist>
            </div>
          </SectionRow>
          <SectionRow>{facts}</SectionRow>
          <SettingsCardFooter>
            <Button type="submit" variant="primary" busy={saving} busyLabel="Saving…">
              Save
            </Button>
          </SettingsCardFooter>
        </form>
      ) : (
        <>
          <SectionRow>
            <DescriptionList
              columns={2}
              items={[
                { term: "Name", detail: workspace.name },
                { term: "Default timezone for new agents", detail: timezone || "UTC" },
              ]}
            />
          </SectionRow>
          <SectionRow>{facts}</SectionRow>
          <SectionRow>
            <ReadOnlyNote variant="block">{readOnlyCopy("admin", "rename the workspace or change its timezone")}</ReadOnlyNote>
          </SectionRow>
        </>
      )}
    </Section>
  );
}

/** The signed-in user's own account: email, and (real sessions only) password + sign out. */
function AccountSection() {
  const { me } = useMe();
  const isBreakGlass = me?.user.id === BREAK_GLASS_USER_ID;

  return (
    <Section
      id="account"
      title="Account"
      description="Your own sign-in."
      aside={
        isBreakGlass ? null : (
          <Button type="button" size="sm" onClick={() => void signOut()}>
            <Icon as={LogOutIcon} size="sm" />
            Sign out
          </Button>
        )
      }
    >
      <SectionRow>
        <DescriptionList
          columns={2}
          items={[
            { term: "Name", detail: me?.user.name || EMPTY_VALUE },
            { term: "Email", detail: me?.user.email, mono: true },
          ]}
        />
      </SectionRow>
      {isBreakGlass ? (
        <SectionRow>
          <ReadOnlyNote variant="block">
            Signed in with the break-glass admin token, so there is no password to change or session to sign out of.
            Sign in at <code className="font-mono">/login</code> to manage a real account.
          </ReadOnlyNote>
        </SectionRow>
      ) : (
        <PasswordForm />
      )}
    </Section>
  );
}

function PasswordForm() {
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("auth/password", { current, new: next });
      toast.success("Password changed. Other sessions were signed out.");
      setCurrent("");
      setNext("");
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col divide-y divide-border">
      <SectionRow className="flex flex-col gap-4">
        {error ? <ErrorBanner error={error} context={{ action: "change your password" }} /> : null}
        <div className="grid max-w-sm gap-4">
          <Field label="Current password" htmlFor="account-current-password">
            <PasswordInput
              id="account-current-password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              disabled={saving}
            />
          </Field>
          <Field label="New password" htmlFor="account-new-password" hint="At least 8 characters.">
            <PasswordInput
              id="account-new-password"
              autoComplete="new-password"
              required
              minLength={8}
              value={next}
              onChange={(event) => setNext(event.target.value)}
              disabled={saving}
            />
          </Field>
        </div>
      </SectionRow>
      <SettingsCardFooter>
        <Button type="submit" variant="primary" busy={saving} busyLabel="Changing password…">
          Change password
        </Button>
      </SettingsCardFooter>
    </form>
  );
}
