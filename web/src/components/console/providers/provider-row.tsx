"use client";

import * as React from "react";
import { toast } from "sonner";

import { Switch } from "@/components/ui/switch";
import { verificationMeta } from "@/components/shared/capability-meta";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { CatalogDialog } from "@/components/console/providers/catalog-dialog";
import { CredentialDialog } from "@/components/console/registry/credential-dialog";
import { useCredentials } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useUpdateProviderSettings } from "@/hooks/useProviders";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

/** A one-sentence badge for capabilities `CapabilityBadge` (WP-0's `shared/capability-badge.tsx`) doesn't have a kind for yet — `text_modality`/`cloud_only` (UI_UX_SPEC-V2-AMENDMENTS §2.2). Flagged in the V2-13 report as a follow-up ask to extend `CAPABILITY_BADGE_META` instead of duplicating this locally. */
export function ProviderRow({ provider, connections }: { provider: ProviderOut; connections: ConnectionOut[] }) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const updateSettings = useUpdateProviderSettings();
  const { data: credentials } = useCredentials(provider.id);
  const caps = provider.capabilities ?? {};
  const verification = verificationMeta(provider);
  const enabled = provider.enabled !== false;
  const needsKey = provider.requires_credential !== false;
  const hasKey = (credentials?.items.length ?? 0) > 0;
  const installedNames = connections.filter((c) => (provider.installed_on ?? []).includes(c.id)).map((c) => c.slug);
  const notInstalledNames = connections.filter((c) => !(provider.installed_on ?? []).includes(c.id)).map((c) => c.slug);
  // Providers/credentials need `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  async function toggleEnabled(next: boolean) {
    try {
      await updateSettings.mutateAsync({ id: provider.id, body: { enabled: next, default_credential_id: provider.default_credential_id ?? null } });
      toast.success(`${provider.label} ${next ? "enabled" : "disabled"}.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-1 gap-3">
        <VendorMark vendor={provider.vendor} size="md" className="mt-0.5" />
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-foreground">{provider.label}</span>
            <span title={verification.help}>
              <StatusChip tone={verification.label === "Verified" ? "success" : "neutral"} size="sm">
                {verification.label}
              </StatusChip>
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {caps.video_input ? <CapabilityBadge kind="vision" /> : null}
            {caps.text_modality ? <CapabilityBadge kind="text-modality" /> : null}
            {caps.tool_calling ? <CapabilityBadge kind="tools" /> : null}
            {caps.cloud_only ? <CapabilityBadge kind="cloud-only" /> : null}
            {provider.requires_credential === false ? <CapabilityBadge kind="no-key" /> : null}
          </div>
          <div className="flex flex-wrap gap-1 text-xs text-muted-foreground">
            {installedNames.map((slug) => (
              <StatusChip key={slug} tone="success" size="sm">
                on {slug}
              </StatusChip>
            ))}
            {notInstalledNames.map((slug) => (
              // `tone="neutral"` alone is the "greyed" look the spec asks for
              // (UI_UX_SPEC-V2-AMENDMENTS §2.2) — an added `opacity-60` here
              // dropped `StatusChip`'s already-AA `bg-muted`/`text-muted-foreground`
              // pair below the WCAG AA contrast threshold (caught by the
              // capture script's axe pass).
              <StatusChip key={slug} tone="neutral" size="sm">
                not on {slug}
              </StatusChip>
            ))}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 sm:flex-col sm:items-end">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{enabled ? "Enabled" : "Disabled"}</span>
          <Switch
            checked={enabled}
            onCheckedChange={(next) => void toggleEnabled(next)}
            aria-label={`Enable ${provider.label}`}
            disabled={!canWrite || updateSettings.isPending}
            title={canWrite ? undefined : writeReason}
          />
        </div>
        {needsKey ? (
          <button
            type="button"
            onClick={() => canWrite && setDialogOpen(true)}
            disabled={!canWrite}
            title={canWrite ? undefined : writeReason}
            className="rounded-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            <CapabilityBadge kind={hasKey ? "key-set" : "key-required"}>{hasKey ? "Key set" : "Key required"}</CapabilityBadge>
          </button>
        ) : null}
        {provider.catalog ? <CatalogDialog provider={provider} /> : null}
      </div>

      <CredentialDialog open={dialogOpen} onOpenChange={setDialogOpen} spec={provider} />
    </div>
  );
}
