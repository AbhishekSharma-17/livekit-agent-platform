import type { Metadata } from "next";

import { ConsoleShell } from "@/components/console/shell/console-shell";

/**
 * Console layout (docs/ui/DESIGN-SYSTEM.md section 7). A server component so
 * `metadata` works; everything interactive lives in `ConsoleShell`. The theme
 * provider is mounted by the root layout.
 */
export const metadata: Metadata = {
  title: {
    template: "%s · LKAP console",
    default: "LKAP console",
  },
};

/**
 * The console renders per request, as it did while this layout read the old
 * sidebar-state cookie: its screens read the query string on the client
 * (`useSearchParams`) and must not be prerendered at build time.
 */
export const dynamic = "force-dynamic";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <ConsoleShell>{children}</ConsoleShell>;
}
