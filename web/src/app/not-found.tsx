import Link from "next/link";

import { Button } from "@/components/ui/button";
import { StateMeter } from "@/components/shared/state-meter";

/**
 * Root 404 (docs/UI_UX_SPEC.md §7.12): branded, with a way back. Catches any
 * URL that does not match a route (outside `/console`, which gets its own
 * `console/not-found.tsx` inside the shell). Same voice as the session
 * surface's unavailable pages (§5.7, WP-8) without repeating their exact
 * copy — this is a generic "nothing here", not "no agent at this address".
 */
export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-4 py-16 text-center text-foreground">
      <StateMeter state="idle" size="md" />
      <div className="flex flex-col gap-2">
        <h1 className="text-[1.375rem] leading-7 font-semibold tracking-[-0.015em]">
          There&apos;s nothing at this address
        </h1>
        <p className="max-w-[42ch] text-sm text-pretty text-muted-foreground">
          Check the link you were given, or head back to where you started.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button asChild size="lg">
          <Link href="/">Go home</Link>
        </Button>
        <Button asChild variant="outline" size="lg">
          <Link href="/console">Open console</Link>
        </Button>
      </div>
    </main>
  );
}
