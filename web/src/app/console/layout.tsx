import { cookies } from "next/headers";
import type { Metadata } from "next";

import { ConsoleShell } from "@/components/console/shell/console-shell";

/**
 * Admin console layout (docs/UI_UX_SPEC.md §3, §7.2). Server component so
 * `metadata` and the sidebar cookie read work; everything interactive lives
 * in `ConsoleShell`. The theme provider is mounted by the root layout.
 */
export const metadata: Metadata = {
  title: {
    template: "%s · LKAP console",
    default: "LKAP console",
  },
};

export default async function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultSidebarOpen = sidebarState !== "false";

  return (
    <div className="min-h-screen bg-background text-foreground">
      <ConsoleShell defaultSidebarOpen={defaultSidebarOpen}>{children}</ConsoleShell>
    </div>
  );
}
