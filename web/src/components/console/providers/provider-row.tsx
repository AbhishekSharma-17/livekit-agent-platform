"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { verificationMeta } from "@/components/shared/capability-meta";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { Highlight } from "@/components/shared/list-search";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus } from "@/components/shared/status-map";
import { VendorMark } from "@/components/shared/vendor-mark";
import { CatalogDialog } from "@/components/console/providers/catalog-dialog";
import { CredentialDialog } from "@/components/console/registry/credential-dialog";
import { composioStatusChip, useComposioStatus } from "@/components/console/tools/apps/use-composio";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import { useUpdateProviderSettings } from "@/hooks/useProviders";
import type { ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

const ROW = "flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:justify-between";

/** "Verified" / "Unverified": a word plus a tone; the longer help is supplementary (the `title`). */
function VerificationPill({ provider }: { provider: ProviderOut }) {
  const verification = verificationMeta(provider);
  return (
    <span title={verification.help}>
      <StatusPill tone={verification.label === "Verified" ? lifecycleStatus("verified").tone : "neutral"} size="sm">
        {verification.label}
      </StatusPill>
    </span>
  );
}

/** A one-sentence badge for capabilities `CapabilityBadge` (WP-0's `shared/capability-badge.tsx`) doesn't have a kind for yet — `text_modality`/`cloud_only` (UI_UX_SPEC-V2-AMENDMENTS §2.2). Flagged in the V2-13 report as a follow-up ask to extend `CAPABILITY_BADGE_META` instead of duplicating this locally. */
export function ProviderRow({
  provider,
  connections,
  query = "",
}: {
  provider: ProviderOut;
  connections: ConnectionOut[];
  /** The list's search query, highlighted in the name. */
  query?: string;
}) {
  // A tool provider (Composio) is enabled/disabled from Tools -> Apps, which
  // also pauses its tools (D-V5-C13, `AppsTab`'s Disable) — the generic
  // Switch below writes `workspace_providers.enabled` directly and skips
  // that (docs/v5/_asks.md #4). Read-only here; the real controls live there.
  if (provider.kind === "tool_provider") {
    return <ToolProviderRow provider={provider} query={query} />;
  }
  // V5-25/V5-28 (docs/v5/_asks.md #154(c)): `web_search`/`sms` run per agent
  // (an agent's own Tools tab picks the vendor and key), not per LiveKit
  // connection — the generic row's Enable switch (`workspace_providers.enabled`)
  // and "installed on <connection>" badges describe a worker-install concept
  // that means nothing here, so this reads like `tool_provider`: read-only,
  // pointing at where it's actually turned on.
  if (provider.kind === "web_search" || provider.kind === "sms") {
    return <NetworkToolProviderRow provider={provider} query={query} where="Picked per agent, in its Tools tab." />;
  }
  // V5-20: a knowledge connection's key is used from Knowledge → Connections, not per
  // LiveKit connection either, so it gets the same read-only row.
  if (provider.kind === "knowledge") {
    return <NetworkToolProviderRow provider={provider} query={query} where="Used through Knowledge → Connections." />;
  }
  return <GenericProviderRow provider={provider} connections={connections} query={query} />;
}

function ProviderName({ provider, query }: { provider: ProviderOut; query: string }) {
  return (
    <span className="text-body font-medium text-foreground">
      <Highlight text={provider.label} query={query} />
    </span>
  );
}

function NetworkToolProviderRow({ provider, where, query }: { provider: ProviderOut; where: string; query: string }) {
  const { data: credentials } = useCredentials(provider.id);
  const hasKey = (credentials?.items.length ?? 0) > 0;
  return (
    <div className={`${ROW} sm:items-center`}>
      <div className="flex min-w-0 flex-1 gap-3">
        <VendorMark vendor={provider.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <ProviderName provider={provider} query={query} />
            <VerificationPill provider={provider} />
            <CapabilityBadge kind={hasKey ? "key-set" : "key-required"}>{hasKey ? "Key set" : "Key required"}</CapabilityBadge>
          </div>
          {provider.notes ? <p className="text-caption text-pretty text-text-secondary">{provider.notes}</p> : null}
        </div>
      </div>
      <p className="shrink-0 text-caption text-text-secondary sm:text-right">{where}</p>
    </div>
  );
}

function ToolProviderRow({ provider, query }: { provider: ProviderOut; query: string }) {
  const { status } = useComposioStatus();
  const chip = composioStatusChip(status);
  return (
    <div className={`${ROW} sm:items-center`}>
      <div className="flex min-w-0 flex-1 gap-3">
        <VendorMark vendor={provider.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <ProviderName provider={provider} query={query} />
            <StatusPill tone={chip.tone} size="sm">
              {chip.label}
            </StatusPill>
          </div>
          {provider.notes ? <p className="text-caption text-pretty text-text-secondary">{provider.notes}</p> : null}
        </div>
      </div>
      <Button asChild size="sm" className="shrink-0">
        <Link href="/console/tools?tab=apps">
          Manage in Tools → Apps
          <ArrowRightIcon aria-hidden="true" />
        </Link>
      </Button>
    </div>
  );
}

/**
 * The generic row: verification, capabilities, where it's installed, and —
 * for admins, since providers and keys are admin writes server-side
 * (`auth/roles.py::ROUTE_POLICY`) — the Enable switch and the key dialog.
 * Everyone else reads the same facts as words (D12). While the role loads
 * the switch shows disabled, so nothing flashes on and then away.
 */
function GenericProviderRow({ provider, connections, query }: { provider: ProviderOut; connections: ConnectionOut[]; query: string }) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const updateSettings = useUpdateProviderSettings();
  const { data: credentials } = useCredentials(provider.id);
  const caps = provider.capabilities ?? {};
  const enabled = provider.enabled !== false;
  const needsKey = provider.requires_credential !== false;
  const hasKey = (credentials?.items.length ?? 0) > 0;
  const installedNames = connections.filter((c) => (provider.installed_on ?? []).includes(c.id)).map((c) => c.slug);
  const notInstalledNames = connections.filter((c) => !(provider.installed_on ?? []).includes(c.id)).map((c) => c.slug);
  const { can: isAdmin, isLoading: roleLoading } = useCan("admin");

  async function toggleEnabled(next: boolean) {
    try {
      await updateSettings.mutateAsync({ id: provider.id, body: { enabled: next, default_credential_id: provider.default_credential_id ?? null } });
      toast.success(`${provider.label} ${next ? "enabled" : "turned off"}.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const keyBadge = <CapabilityBadge kind={hasKey ? "key-set" : "key-required"}>{hasKey ? "Key set" : "Key required"}</CapabilityBadge>;

  return (
    <div className={`${ROW} sm:items-start`}>
      <div className="flex min-w-0 flex-1 gap-3">
        <VendorMark vendor={provider.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <ProviderName provider={provider} query={query} />
            <VerificationPill provider={provider} />
          </div>
          <div className="flex flex-wrap gap-1">
            {caps.video_input ? <CapabilityBadge kind="vision" /> : null}
            {caps.text_modality ? <CapabilityBadge kind="text-modality" /> : null}
            {caps.tool_calling ? <CapabilityBadge kind="tools" /> : null}
            {caps.cloud_only ? <CapabilityBadge kind="cloud-only" /> : null}
            {provider.requires_credential === false ? <CapabilityBadge kind="no-key" /> : null}
          </div>
          <div className="flex flex-wrap gap-1">
            {installedNames.map((slug) => (
              <StatusPill key={slug} tone="success" size="sm">
                on {slug}
              </StatusPill>
            ))}
            {notInstalledNames.map((slug) => (
              // `tone="neutral"` alone is the "greyed" look the spec asks for
              // (UI_UX_SPEC-V2-AMENDMENTS §2.2) — an added opacity here would
              // drop the pill's AA text pair below the WCAG threshold (caught
              // by the capture script's axe pass).
              <StatusPill key={slug} tone="neutral" size="sm">
                not on {slug}
              </StatusPill>
            ))}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 sm:flex-col sm:items-end">
        {isAdmin || roleLoading ? (
          <div className="flex items-center gap-2">
            <span className="text-caption text-text-secondary">{enabled ? "Enabled" : "Off"}</span>
            <Switch
              checked={enabled}
              onCheckedChange={(next) => void toggleEnabled(next)}
              aria-label={`Enable ${provider.label}`}
              disabled={roleLoading || updateSettings.isPending}
            />
          </div>
        ) : (
          <StatusPill tone={enabled ? "success" : "neutral"} size="sm">
            {enabled ? "Enabled" : "Off"}
          </StatusPill>
        )}
        {needsKey ? (
          isAdmin ? (
            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              aria-label={`${hasKey ? "Key set" : "Key required"}: manage the ${provider.label} key`}
              className="rounded-pill"
            >
              {keyBadge}
            </button>
          ) : (
            keyBadge
          )
        ) : null}
        {provider.catalog ? <CatalogDialog provider={provider} /> : null}
      </div>

      {isAdmin ? <CredentialDialog open={dialogOpen} onOpenChange={setDialogOpen} spec={provider} /> : null}
    </div>
  );
}
