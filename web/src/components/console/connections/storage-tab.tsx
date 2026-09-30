import { Alert } from "@/components/ui/alert";
import { MetaList } from "@/components/shared/data-display";
import { Section, SectionRow } from "@/components/shared/section";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * Detail → Storage tab (UI_UX_SPEC-V2-AMENDMENTS §2.1: "pick or create a
 * storage config; Test writes and deletes a 1 KB object").
 *
 * **Deviation, reported per the package brief's "stop and report" rule for
 * design questions the docs don't answer**: `docs/v2/_asks.md` #24 records
 * that `GET/PUT /v1/connections/{id}/storage` was never implemented (`PUT
 * /v1/connections/{id}` with `storage_config_id` covers assignment), and
 * there is no `/v1/storage-configs` CRUD router at all yet — confirmed
 * against `api/src/lkap_api/routers/`. `storage_configs` (V2-14's Settings →
 * Storage tab) is a `ComingSoonTab` placeholder today
 * (`components/console/settings/settings-tabs.tsx`). This tab can therefore
 * only show the connection's current `storage_config_id` (already visible
 * via `PUT /v1/connections/{id}`) and point at where a config will be
 * created, rather than a picker over a real list or a "Test" action against
 * one — there is nothing to pick from or test yet. The "unavailable" state
 * (docs/ui/DESIGN-SYSTEM.md section 8.8) says so and what happens meanwhile.
 */
export function StorageTab({ connection }: { connection: ConnectionOut }) {
  return (
    <Section id="connection-storage" title="Recording storage" description="Where this connection's recordings are kept.">
      <SectionRow className="flex flex-col gap-4">
        <Alert tone="info" title="Custom storage isn't available yet">
          Custom storage for recordings can&apos;t be set up from the console yet. Until it can, this connection uses the
          platform&apos;s default storage.
        </Alert>
        <MetaList
          items={[
            {
              term: "Storage for this connection",
              // Never an internal id (spec section 3): there is no storage-config name to show yet.
              value: connection.storage_config_id ? "Custom storage" : "Platform default",
            },
          ]}
        />
      </SectionRow>
    </Section>
  );
}
