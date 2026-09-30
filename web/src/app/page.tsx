import Link from "next/link";

import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";

/**
 * Home page (docs/UI_UX_SPEC.md §3.4, §7.12). Not a marketing site: a
 * wordmark, one sentence, two actions, an inline disclosure, a
 * server-rendered environment line from the public `GET /v1/health`
 * endpoint (no admin token — see `src/app/api/console/[...path]/route.ts`
 * for the *authenticated* console proxy this page deliberately does not
 * use), and a footer. Redirecting `/` to `/console` is rejected by the spec:
 * this is where a visitor lands from a shared link with the wrong path.
 *
 * Theme: the root layout mounts the one app-wide `ThemeProvider` (UI-1), so
 * this page follows the system setting, or the person's saved choice, like
 * every other route, with no flash on load. Every colour below is a token,
 * so it reads correctly in both themes. No theme control lives on this page.
 *
 * Actions (docs/ui/DESIGN-SYSTEM.md sections 6.1 and 9): "Sign in" (to
 * `/login`, v2 amendment §3) is secondary, then "Open console" is the one
 * primary, placed last. An unreachable API is said plainly, with what that
 * means and what to do (section 8, "Offline or unavailable").
 */

interface HealthSummary {
  reachable: boolean;
  version?: string;
  packCount?: number;
}

async function getHealthSummary(): Promise<HealthSummary> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) return { reachable: false };

  try {
    const response = await fetch(`${base.replace(/\/+$/, "")}/v1/health`, {
      cache: "no-store",
    });
    if (!response.ok) return { reachable: false };

    const data = (await response.json()) as {
      ok?: boolean;
      version?: string;
      packs?: string[];
    };
    return {
      reachable: Boolean(data.ok),
      version: typeof data.version === "string" ? data.version : undefined,
      packCount: Array.isArray(data.packs) ? data.packs.length : undefined,
    };
  } catch {
    return { reachable: false };
  }
}

function EnvironmentLine({ health }: { health: HealthSummary }) {
  const parts: string[] = [];
  if (health.version) parts.push(`v${health.version}`);
  if (health.packCount !== undefined) {
    parts.push(`${health.packCount} pack${health.packCount === 1 ? "" : "s"} loaded`);
  }
  parts.push(health.reachable ? "API reachable" : "API unreachable");

  return (
    <div className="flex flex-col items-center gap-1">
      <p className="font-mono text-caption tabular-nums text-text-secondary">{parts.join(" · ")}</p>
      {health.reachable ? null : (
        <p className="text-caption text-text-secondary">
          Sign-in won&apos;t work until the API is reachable. Reload this page to check again.
        </p>
      )}
    </div>
  );
}

export default async function Home() {
  const health = await getHealthSummary();

  return (
    <main className="flex min-h-dvh flex-col bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-lg flex-1 flex-col items-center justify-center gap-8 px-4 py-16 text-center">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="inline-flex">
            <StateMeter state="idle" size="md" />
          </span>
          <h1 className="text-page font-semibold tracking-[-0.018em]">LKAP</h1>
        </div>

        <p className="max-w-[42ch] text-body text-text-secondary">
          Configure real-time voice and video agents on LiveKit Cloud, then
          hand out a link.
        </p>

        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button asChild variant="secondary" size="lg">
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild variant="primary" size="lg">
            <Link href="/console">Open console</Link>
          </Button>
        </div>

        <Collapsible className="w-full max-w-md text-left">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="group mx-auto flex items-center gap-1.5 rounded-sm text-control font-medium text-text-secondary underline decoration-dotted underline-offset-4 transition-colors duration-(--duration-fast) ease-out hover:text-foreground aria-expanded:text-foreground"
            >
              How session pages work
              <Icon
                as={ChevronDownIcon}
                size="sm"
                className="transition-transform duration-(--duration-base) ease-out group-aria-expanded:rotate-180"
              />
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-3 rounded-lg border border-border bg-card p-4 text-left text-label text-text-secondary">
            <p>
              Every published agent answers at{" "}
              <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-foreground">
                /s/&lt;slug&gt;
              </code>
              . The slug comes from the agent&apos;s settings in the console.
            </p>
            <p className="mt-2">
              Before publishing, open the same address with{" "}
              <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-foreground">
                ?mode=test
              </code>{" "}
              from the console to try a draft agent.
            </p>
            <p className="mt-2">
              Publishing an agent makes its session page reachable to anyone
              who has the link.
            </p>
          </CollapsibleContent>
        </Collapsible>
      </div>

      <footer className="border-t border-border px-4 py-6">
        <div className="mx-auto flex w-full max-w-lg items-center justify-center text-center">
          <EnvironmentLine health={health} />
        </div>
      </footer>
    </main>
  );
}
