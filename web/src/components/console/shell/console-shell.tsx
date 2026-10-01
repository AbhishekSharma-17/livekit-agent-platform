import { cookies } from "next/headers";

import { SIDEBAR_COOKIE, parseSidebarState } from "@/components/ui/sidebar-state";
import { ConsoleShellClient } from "./console-shell-client";

/**
 * The server half of the console shell. It reads the remembered sidebar
 * state from its cookie mirror (`components/ui/sidebar-state.ts`) so the
 * first paint already has the right width: no flash of the full sidebar
 * before the rail, and no jump after hydration. Everything interactive is in
 * `ConsoleShellClient`. The console layout renders per request, so reading
 * the cookie costs nothing extra.
 */
export async function ConsoleShell({ children }: { children: React.ReactNode }) {
  const store = await cookies();
  const collapsed = parseSidebarState(store.get(SIDEBAR_COOKIE)?.value) === "collapsed";
  return <ConsoleShellClient defaultCollapsed={collapsed}>{children}</ConsoleShellClient>;
}
