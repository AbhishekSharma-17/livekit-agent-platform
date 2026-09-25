import { Suspense } from "react";
import Link from "next/link";
import type { Metadata } from "next";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
import { ProvidersCatalog } from "@/components/console/providers/providers-catalog";
import { WorkspacePricesButton } from "@/components/console/settings/workspace-prices-dialog";

export const metadata: Metadata = { title: "Providers" };

/**
 * `/console/providers?kind=` (UI_UX_SPEC-V2-AMENDMENTS §1, §2.2) — replaces
 * "Credentials" as the connect group's entry point; the flat key list stays
 * reachable at `/console/keys` (WP-4) via the header link.
 */
export default function ConsoleProvidersPage() {
  return (
    <div>
      <PageHeader
        title="Providers"
        description="Every provider in the registry, grouped by kind — enable, add a key and preview its catalog."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <WorkspacePricesButton />
            <Button asChild variant="outline">
              <Link href="/console/keys">Manage keys</Link>
            </Button>
          </div>
        }
      />
      <Suspense>
        <ProvidersCatalog />
      </Suspense>
    </div>
  );
}
