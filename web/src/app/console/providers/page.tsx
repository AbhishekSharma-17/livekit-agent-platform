import { Suspense } from "react";
import Link from "next/link";
import type { Metadata } from "next";
import { KeyRoundIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/shared/page-header";
import { ProvidersCatalog, ProvidersCatalogSkeleton } from "@/components/console/providers/providers-catalog";
import { WorkspacePricesButton } from "@/components/console/settings/workspace-prices-dialog";

export const metadata: Metadata = { title: "Providers" };

/**
 * `/console/providers?kind=` (UI_UX_SPEC-V2-AMENDMENTS §1, §2.2) — replaces
 * "Credentials" as the connect group's entry point; the flat key list stays
 * reachable at `/console/keys` (WP-4) via the header link. The page template
 * (docs/ui/DESIGN-SYSTEM.md section 7.3): "Your prices" (admins only, S6)
 * then "Manage keys" as the one primary, last — keys are what a provider
 * needs before an agent can use it.
 */
export default function ConsoleProvidersPage() {
  return (
    <Page>
      <PageHeader
        title="Providers"
        description="Every speech, language, voice and tool provider this server offers, grouped by kind."
        actions={
          <>
            <WorkspacePricesButton />
            <Button asChild variant="primary">
              <Link href="/console/keys">
                <KeyRoundIcon aria-hidden="true" />
                Manage keys
              </Link>
            </Button>
          </>
        }
      />
      <Suspense fallback={<ProvidersCatalogSkeleton />}>
        <ProvidersCatalog />
      </Suspense>
    </Page>
  );
}
