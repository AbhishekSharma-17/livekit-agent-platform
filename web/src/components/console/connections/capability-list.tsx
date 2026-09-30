"use client";

import { CAPABILITY_FALSE_REASON, connectionCapabilityChips } from "@/components/shared/capability-meta";
import { DescriptionList } from "@/components/shared/description-list";
import { Icon } from "@/components/shared/icon";
import { StatusPill } from "@/components/shared/status-chip";
import type { ConnectionCapabilities } from "@/contracts/lkap-contracts";

/**
 * The capability `DescriptionList` a connection's test result renders into
 * (UI_UX_SPEC-V2-AMENDMENTS §2.1: "reasons for false flags"). A present flag
 * reads "Available" in the success tone; an absent one reads "Not available"
 * in neutral, plus the fix sentence from `CAPABILITY_FALSE_REASON`. Always a
 * word plus a tone, never colour alone.
 */
export function CapabilityList({ capabilities }: { capabilities: ConnectionCapabilities | undefined }) {
  const chips = connectionCapabilityChips(capabilities);
  return (
    <DescriptionList
      columns={2}
      items={chips.map((chip) => ({
        term: (
          <span className="flex items-center gap-1.5">
            <Icon as={chip.meta.icon} size="sm" />
            {chip.meta.label}
          </span>
        ),
        detail: chip.present ? (
          <StatusPill tone="success" size="sm">
            {chip.detail ? `${chip.detail}` : "Available"}
          </StatusPill>
        ) : (
          <span className="flex flex-col gap-1">
            <StatusPill tone="neutral" size="sm">
              Not available
            </StatusPill>
            <span className="text-caption text-pretty text-text-secondary">{CAPABILITY_FALSE_REASON[chip.key]}</span>
          </span>
        ),
      }))}
    />
  );
}
