"use client";

import { CAPABILITY_FALSE_REASON, connectionCapabilityChips } from "@/components/shared/capability-meta";
import { DescriptionList } from "@/components/shared/description-list";
import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import type { ConnectionCapabilities } from "@/contracts/lkap-contracts";

/**
 * The capability `DescriptionList` a connection's test result renders into
 * (UI_UX_SPEC-V2-AMENDMENTS §2.1: "reasons for false flags"). A present flag
 * gets a green chip; an absent one gets a neutral chip plus the fix sentence
 * from `CAPABILITY_FALSE_REASON`.
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
          <StatusChip tone="success" size="sm" dot>
            {chip.detail ? `${chip.detail}` : "Available"}
          </StatusChip>
        ) : (
          <span className="flex flex-col gap-1">
            <StatusChip tone="neutral" size="sm">
              Not available
            </StatusChip>
            <span className="text-xs text-pretty text-muted-foreground">{CAPABILITY_FALSE_REASON[chip.key]}</span>
          </span>
        ),
      }))}
    />
  );
}
