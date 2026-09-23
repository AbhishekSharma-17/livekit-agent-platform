import { Alert, AlertDescription } from "@/components/ui/alert";
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
 * one — there is nothing to pick from or test yet.
 */
export function StorageTab({ connection }: { connection: ConnectionOut }) {
  return (
    <div className="flex flex-col gap-4">
      <Alert>
        <AlertDescription>
          Storage configs aren&apos;t available to create yet — <span className="font-mono">/v1/storage-configs</span>{" "}
          has no CRUD endpoints (docs/v2/_asks.md #24), and Settings → Storage is a placeholder pending V2-14. Once a
          config exists it can be assigned here with <span className="font-mono">PUT /v1/connections/&#123;id&#125;</span>.
        </AlertDescription>
      </Alert>
      <p className="text-sm text-muted-foreground">
        Current storage config: <span className="font-mono">{connection.storage_config_id ?? "none (platform default)"}</span>
      </p>
    </div>
  );
}
