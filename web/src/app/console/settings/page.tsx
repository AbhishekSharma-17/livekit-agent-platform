import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { Page, PageHeader } from "@/components/shared/page-header";
import { movedSettingsTab } from "@/components/console/settings/moved-tabs";
import { SettingsTabs } from "@/components/console/settings/settings-tabs";

export const metadata: Metadata = { title: "Settings" };

/**
 * `/console/settings?tab=` — the Settings archetype (docs/ui/DESIGN-SYSTEM.md
 * section 7.4, decision D5 in docs/ui/AUDIT.md): a wide page with a vertical
 * section nav on the left and one section's cards on the right. `?tab=`
 * deep links keep working; each card saves on its own, so the header has no
 * page-level action. A section that moved to another page redirects there
 * (`?tab=knowledge-connections` → `/console/knowledge?tab=connections`).
 */
export default async function ConsoleSettingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const moved = movedSettingsTab((await searchParams).tab);
  if (moved) redirect(moved);
  return (
    <Page width="wide">
      <PageHeader title="Settings" description="Manage this workspace, its team, keys and webhooks, and your own account." />
      <SettingsTabs />
    </Page>
  );
}
