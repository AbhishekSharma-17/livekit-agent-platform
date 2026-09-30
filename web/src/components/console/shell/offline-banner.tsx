"use client";

import { Alert } from "@/components/ui/alert";
import { useOnlineStatus } from "@/hooks/use-online-status";

/**
 * The console's one offline notice (docs/ui/DESIGN-SYSTEM.md section 8.8:
 * "say so, and say what will happen"). Mounted once by the shell under the
 * top bar, so screens don't each need their own.
 *
 * What it promises is what the query client does: while the browser is
 * offline, reads and saves pause (TanStack Query's default `online` network
 * mode) and carry on once the connection is back, when every page refetches.
 * While it shows, the shell's "Can't reach the API" alert stays hidden: one
 * cause, one message.
 *
 * The polite live region is always mounted, so screen readers hear the notice
 * when it appears.
 */
export function OfflineBanner() {
  const online = useOnlineStatus();
  return (
    <div data-slot="offline-banner" aria-live="polite" className={online ? "contents" : "px-8 pt-4 max-[900px]:px-5 max-[640px]:px-4"}>
      {online ? null : (
        <Alert tone="warning" title="You're offline" className="mx-auto max-w-[1440px]">
          Changes save when you&rsquo;re back online. Reconnecting&hellip;
        </Alert>
      )}
    </div>
  );
}
