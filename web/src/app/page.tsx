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
 * Renders outside the console's `ThemeProvider` (mounted only in
 * `app/console/layout.tsx`), so a hard load is always the light theme; a
 * client-side navigation here from a dark console can leave `html.dark`
 * set, so every color below must come from tokens (never a hard-coded
 * light-only value) so the page still reads correctly either way. No theme
 * control lives on this page.
 *
 * v2 amendment (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §3, "WP-11 Home/not-found:
 * Unchanged | Home links to `/login`"): the "Sign in" link below points at
 * `/login`, which ships with V2-14 and does not exist in this tree yet. Until
 * then it resolves to the branded `app/not-found.tsx` (with a way back), the
 * same way any link to a not-yet-built route would.
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
    <p className="font-mono text-[0.8125rem] leading-[1.125rem] tabular-nums text-muted-foreground">
      {parts.join(" · ")}
    </p>
  );
}

export default async function Home() {
  const health = await getHealthSummary();

  return (
    <main className="flex min-h-screen flex-col bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-lg flex-1 flex-col items-center justify-center gap-8 px-4 py-16 text-center">
        <div className="flex items-center gap-2.5">
          <StateMeter state="idle" size="md" />
          <h1 className="text-[1.375rem] leading-7 font-semibold tracking-[-0.015em]">
            LKAP
          </h1>
        </div>

        <p className="max-w-[42ch] text-base text-pretty text-muted-foreground">
          Configure real-time voice and video agents on LiveKit Cloud, then
          hand out a link.
        </p>

        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button asChild size="lg">
            <Link href="/console">Open console</Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link href="/login">Sign in</Link>
          </Button>
        </div>

        <Collapsible className="w-full max-w-md text-left">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="group mx-auto flex items-center gap-1.5 rounded-sm text-sm font-medium text-muted-foreground underline decoration-dotted underline-offset-4 outline-none transition-colors duration-(--dur-2) ease-out hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-expanded:text-foreground"
            >
              How session pages work
              <Icon
                as={ChevronDownIcon}
                size="sm"
                className="transition-transform duration-(--dur-2) ease-out group-aria-expanded:rotate-180"
              />
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-3 rounded-lg border border-border bg-card p-4 text-left text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
            <p>
              Every published agent answers at{" "}
              <code className="rounded-xs bg-muted px-1 py-0.5 font-mono text-foreground">
                /s/&lt;slug&gt;
              </code>
              . The slug comes from the agent&apos;s settings in the console.
            </p>
            <p className="mt-2">
              Before publishing, open the same address with{" "}
              <code className="rounded-xs bg-muted px-1 py-0.5 font-mono text-foreground">
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
