import { PackageOpenIcon } from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";

/**
 * Placeholder for a Settings tab whose content V2-14 owns (docs/v2/PLAN-V2.md
 * V2-14: "team/members/roles, API keys, webhooks, storage configs,
 * settings"). Kept deliberately generic — this is not the tab's final copy.
 */
export function ComingSoonTab({ what }: { what: string }) {
  return (
    <EmptyState
      icon={PackageOpenIcon}
      title={`${what} isn't set up yet`}
      description="This tab is reserved — it fills in once workspace management ships."
    />
  );
}
