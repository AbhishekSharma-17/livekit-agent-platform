import Link from "next/link";
import { HardDriveIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Section, SectionRow } from "@/components/shared/section";

/**
 * `storage_configs` exists in the schema (CONTRACTS-V2 §1.2) but Phase 1
 * shipped no workspace-level CRUD for it — only `PUT /v1/connections/{id}`
 * accepts `storage_config_id` (V2-13's surface; ask #24: "not implemented:
 * `GET/PUT /v1/connections/{id}/storage`"), and recordings pick up whichever
 * config a connection points at. Rather than a silent "coming soon" (this
 * tab's real gap is worth naming), this says exactly what exists today and
 * where to reach it. Revisit once a `/v1/storage-configs` router ships.
 */
export function StorageTab() {
  return (
    <Section
      id="storage"
      title="Storage"
      description="Where recordings and knowledge-base uploads are stored."
    >
      <SectionRow>
        <EmptyState
          icon={HardDriveIcon}
          title="No workspace-level storage settings yet"
          description="Storage configs are attached per connection today, not managed from Settings. Open a connection to point its recordings at S3-compatible storage or the dev-only local backend."
          action={
            <Button asChild variant="outline" size="sm">
              <Link href="/console/connections">Go to Connections</Link>
            </Button>
          }
        />
      </SectionRow>
    </Section>
  );
}
