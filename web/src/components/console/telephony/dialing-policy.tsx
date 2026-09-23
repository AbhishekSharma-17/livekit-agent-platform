"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Section, StatusChip } from "@/components/shared";
import { SectionRow } from "@/components/shared/section";
import { errorMessage } from "@/components/console/shared/error-banner";
import {
  useActiveWorkspace,
  useInvalidateSettings,
  useWorkspace,
} from "@/components/console/settings/use-settings-queries";
import { api } from "@/lib/api";

/**
 * The workspace's outbound dialing policy (ruling R-V2-23), stored in
 * `workspaces.settings.telephony` and edited through `PUT /v1/workspaces/{id}`
 * (admins and owners only; the route merges `settings` one level deep, so the
 * whole `telephony` object is sent). Default deny: with no allowed prefix,
 * nobody can place a call or transfer one, from the console, the API or the
 * agent's `transfer_call` tool. Premium-rate and satellite ranges stay blocked
 * whatever is listed here.
 *
 * V2-22: `+1` admits the United States and Canada only (ruling R-V2-29); the
 * Caribbean countries and the US territories each need their own prefix, and
 * the card warns when `+1` is the only NANP entry. A `sip:` address with a
 * number as its user must also name a listed SIP host (R-V2-28).
 */
export interface DialingPolicy {
  allowed_prefixes: string[];
  allowed_sip_hosts: string[];
  max_calls_per_min: number;
  max_concurrent_outbound: number;
}

export const DEFAULT_DIALING_POLICY: DialingPolicy = {
  allowed_prefixes: [],
  allowed_sip_hosts: [],
  max_calls_per_min: 10,
  max_concurrent_outbound: 5,
};

const PREFIX_PATTERN = /^\+[0-9]{1,15}$/;
const HOST_PATTERN = /^[a-z0-9.-]{1,253}$/;

/** The stored policy, with defaults for anything missing or malformed. */
export function policyFromSettings(settings: Record<string, unknown> | undefined): DialingPolicy {
  const raw = settings?.telephony;
  if (!raw || typeof raw !== "object") return { ...DEFAULT_DIALING_POLICY };
  const value = raw as Record<string, unknown>;
  const list = (key: string) =>
    Array.isArray(value[key]) ? (value[key] as unknown[]).filter((v): v is string => typeof v === "string") : [];
  const count = (key: keyof DialingPolicy, fallback: number) =>
    typeof value[key] === "number" && Number.isInteger(value[key]) ? (value[key] as number) : fallback;
  return {
    allowed_prefixes: list("allowed_prefixes"),
    allowed_sip_hosts: list("allowed_sip_hosts"),
    max_calls_per_min: count("max_calls_per_min", DEFAULT_DIALING_POLICY.max_calls_per_min),
    max_concurrent_outbound: count("max_concurrent_outbound", DEFAULT_DIALING_POLICY.max_concurrent_outbound),
  };
}

/** Shown while `+1` is the only NANP prefix listed (R-V2-29). */
export const PLUS_ONE_WARNING =
  "+1 covers the US and Canada; Caribbean and territory numbers need their own prefix, for example +1876.";

/** Whether `+1` is listed with no longer `+1…` prefix beside it. */
export function plusOneAlone(prefixes: string[]): boolean {
  return prefixes.includes("+1") && !prefixes.some((prefix) => prefix.startsWith("+1") && prefix !== "+1");
}

/** Split a comma / newline list, dropping spaces and dashes people paste into numbers. */
export function splitList(value: string, { numbers }: { numbers: boolean }): string[] {
  return value
    .split(/[,\n]/)
    .map((part) => (numbers ? part.replace(/[\s().-]/g, "") : part.trim().toLowerCase()))
    .filter(Boolean);
}

interface Draft {
  prefixes: string;
  hosts: string;
  perMin: string;
  concurrent: string;
}

function draftOf(policy: DialingPolicy): Draft {
  return {
    prefixes: policy.allowed_prefixes.join(", "),
    hosts: policy.allowed_sip_hosts.join(", "),
    perMin: String(policy.max_calls_per_min),
    concurrent: String(policy.max_concurrent_outbound),
  };
}

/** Validate a draft; returns the policy to save or per-field messages. */
export function parseDraft(draft: Draft): { policy: DialingPolicy | null; errors: Partial<Record<keyof Draft, string>> } {
  const errors: Partial<Record<keyof Draft, string>> = {};
  const prefixes = splitList(draft.prefixes, { numbers: true });
  const badPrefix = prefixes.find((p) => !PREFIX_PATTERN.test(p));
  if (badPrefix) errors.prefixes = `"${badPrefix}" is not a prefix like +1 or +4420`;
  const hosts = splitList(draft.hosts, { numbers: false });
  const badHost = hosts.find((h) => !HOST_PATTERN.test(h));
  if (badHost) errors.hosts = `"${badHost}" is not a host name like pbx.example.com`;
  const perMin = Number(draft.perMin);
  if (!Number.isInteger(perMin) || perMin < 0) errors.perMin = "Whole numbers only";
  const concurrent = Number(draft.concurrent);
  if (!Number.isInteger(concurrent) || concurrent < 0) errors.concurrent = "Whole numbers only";
  if (Object.keys(errors).length) return { policy: null, errors };
  return {
    policy: {
      allowed_prefixes: Array.from(new Set(prefixes)),
      allowed_sip_hosts: Array.from(new Set(hosts)),
      max_calls_per_min: perMin,
      max_concurrent_outbound: concurrent,
    },
    errors,
  };
}

export function DialingPolicyCard() {
  const { workspace: membership } = useActiveWorkspace();
  const workspaceQuery = useWorkspace(membership?.id);
  const invalidate = useInvalidateSettings();
  const stored = policyFromSettings(workspaceQuery.data?.settings);
  const storedKey = JSON.stringify(stored);
  const canEdit = membership?.role === "admin" || membership?.role === "owner";

  const [draft, setDraft] = React.useState<Draft>(() => draftOf(stored));
  const [errors, setErrors] = React.useState<Partial<Record<keyof Draft, string>>>({});
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    setDraft(draftOf(JSON.parse(storedKey) as DialingPolicy));
    setErrors({});
  }, [storedKey]);

  const enabled = stored.allowed_prefixes.length > 0;
  const warnPlusOne = plusOneAlone(splitList(draft.prefixes, { numbers: true }));
  const ids = { prefixes: "dialing-prefixes", hosts: "dialing-hosts", perMin: "dialing-per-min", concurrent: "dialing-concurrent" };

  async function onSave(event: React.FormEvent) {
    event.preventDefault();
    if (!membership) return;
    const parsed = parseDraft(draft);
    setErrors(parsed.errors);
    if (!parsed.policy) return;
    setSaving(true);
    try {
      await api.put(`workspaces/${membership.id}`, { settings: { telephony: parsed.policy } });
      invalidate(membership.id);
      toast.success("Dialing policy saved");
    } catch (err) {
      toast.error(`Couldn't save the dialing policy — ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  const set = (key: keyof Draft) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setDraft((current) => ({ ...current, [key]: event.target.value }));

  return (
    <Section
      id="telephony-dialing-policy"
      title="Outbound dialing policy"
      description="Which numbers calls and transfers may reach. Premium-rate and satellite numbers are always blocked."
      aside={
        <StatusChip tone={enabled ? "success" : "warning"} size="sm">
          {enabled ? "Outbound calls on" : "Outbound calls off"}
        </StatusChip>
      }
    >
      <form onSubmit={onSave} aria-label="Outbound dialing policy">
        <SectionRow className="flex flex-col gap-4">
          {!enabled ? (
            <p className="text-sm text-muted-foreground">
              No number prefix is allowed yet, so nobody can place or transfer a call from this workspace.
            </p>
          ) : null}
          <Field
            label="Allowed number prefixes"
            htmlFor={ids.prefixes}
            hint="Country or area prefixes, separated by commas, for example +1, +4420."
            error={errors.prefixes}
          >
            <Input
              id={ids.prefixes}
              value={draft.prefixes}
              onChange={set("prefixes")}
              placeholder="+1, +4420"
              className="font-mono text-[0.8125rem]"
              autoComplete="off"
              spellCheck={false}
              readOnly={!canEdit}
            />
          </Field>
          {warnPlusOne ? (
            <p role="note" className="text-[0.8125rem] text-pretty text-warning-text">
              {PLUS_ONE_WARNING}
            </p>
          ) : null}
          <Field
            label="Allowed SIP hosts"
            htmlFor={ids.hosts}
            hint="Hosts a sip: address may reach, for example your PBX. To transfer to a phone number use +E.164; use sip: only for a listed SIP host (a sip: address with a number needs its host listed too)."
            error={errors.hosts}
            optional
          >
            <Input
              id={ids.hosts}
              value={draft.hosts}
              onChange={set("hosts")}
              placeholder="pbx.example.com"
              className="font-mono text-[0.8125rem]"
              autoComplete="off"
              spellCheck={false}
              readOnly={!canEdit}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Calls per minute" htmlFor={ids.perMin} error={errors.perMin}>
              <Input
                id={ids.perMin}
                value={draft.perMin}
                onChange={set("perMin")}
                inputMode="numeric"
                readOnly={!canEdit}
              />
            </Field>
            <Field label="Outbound calls at once" htmlFor={ids.concurrent} error={errors.concurrent}>
              <Input
                id={ids.concurrent}
                value={draft.concurrent}
                onChange={set("concurrent")}
                inputMode="numeric"
                readOnly={!canEdit}
              />
            </Field>
          </div>
          {canEdit ? (
            <div>
              <Button type="submit" disabled={saving || !membership}>
                {saving ? "Saving…" : "Save policy"}
              </Button>
            </div>
          ) : (
            <p className="text-[0.8125rem] text-muted-foreground">Only admins and owners can change the policy.</p>
          )}
        </SectionRow>
      </form>
    </Section>
  );
}
