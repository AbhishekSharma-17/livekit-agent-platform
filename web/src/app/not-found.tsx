import Link from "next/link";

import { Button } from "@/components/ui/button";
import { StateMeter } from "@/components/shared/state-meter";

/**
 * Root 404 (docs/UI_UX_SPEC.md §7.12): branded, with a way back. Catches any
 * URL that does not match a route (outside `/console`, which gets its own
 * `console/not-found.tsx` inside the shell). Same voice as the session
 * surface's unavailable pages (§5.7, WP-8) without repeating their exact
 * copy — this is a generic "nothing here", not "no agent at this address".
 * "Open console" is secondary and "Go home" the one primary, placed last
 * (docs/ui/DESIGN-SYSTEM.md section 6.1).
 */
export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-background px-4 py-16 text-center text-foreground">
      <span aria-hidden="true" className="inline-flex">
        <StateMeter state="idle" size="md" />
      </span>
      <div className="flex flex-col gap-2">
        <h1 className="text-page font-semibold tracking-[-0.018em]">There&apos;s nothing at this address</h1>
        <p className="max-w-[42ch] text-body text-text-secondary">
          Check the link you were given, or head back to where you started.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button asChild variant="secondary" size="lg">
          <Link href="/console">Open console</Link>
        </Button>
        <Button asChild variant="primary" size="lg">
          <Link href="/">Go home</Link>
        </Button>
      </div>
    </main>
  );
}
