import Link from "next/link";
import { ArrowRightIcon, HardDriveIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Section, SectionRow } from "@/components/shared/section";

/**
 * `storage_configs` exists in the schema (CONTRACTS-V2 §1.2) but Phase 1
 * shipped no workspace-level CRUD for it — only `PUT /v1/connections/{id}`
 * accepts `storage_config_id` (V2-13's surface; ask #24: "not implemented:
 * `GET/PUT /v1/connections/{id}/storage`"), and recordings pick up whichever
 * config a connection points at. So this is the section's "unavailable"
 * state (docs/ui/DESIGN-SYSTEM.md section 8.8): it says what exists today and
 * where to reach it, with one action. Revisit once a `/v1/storage-configs`
 * router ships.
 */
export function StorageTab() {
  return (
    <Section id="storage" title="Storage" description="Where recordings and knowledge-base uploads are stored.">
      <SectionRow>
        <EmptyState
          variant="plain"
          icon={HardDriveIcon}
          title="Storage is set per connection"
          description="Open a connection to send its recordings to S3-compatible storage, or to the local store for development."
          action={
            <Button asChild size="sm">
              <Link href="/console/connections">
                Go to Connections
                <ArrowRightIcon aria-hidden="true" />
              </Link>
            </Button>
          }
        />
      </SectionRow>
    </Section>
  );
}
