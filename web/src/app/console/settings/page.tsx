import type { Metadata } from "next";

import { PageHeader } from "@/components/shared/page-header";
import { SettingsTabs } from "@/components/console/settings/settings-tabs";

export const metadata: Metadata = { title: "Settings" };

/**
 * `/console/settings?tab=` — docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1, §2.5:
 * settings becomes tabbed. See `settings-tabs.tsx` for which tabs this
 * package fills in versus reserves for V2-14.
 */
export default function ConsoleSettingsPage() {
  return (
    <div>
      <PageHeader title="Settings" />
      <SettingsTabs />
    </div>
  );
}
