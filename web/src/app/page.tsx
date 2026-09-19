import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Home page. Points to the two real surfaces: the admin console (create and
 * configure agents) and the session pages that published agents answer on
 * (`(session)/s/[slug]`) — see docs/REVIEW-FINAL.md F-23.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center gap-6 p-8">
      <Badge variant="secondary">LiveKit Agent Platform</Badge>
      <h1 className="text-center text-3xl font-semibold tracking-tight">
        LiveKit Agent Platform
      </h1>
      <p className="text-center text-muted-foreground">
        Configure and run real-time voice + video agents on LiveKit Cloud.
      </p>
      <div className="grid w-full gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Console</CardTitle>
            <CardDescription>
              Create and configure agents, credentials, tools and knowledge
              bases.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              className="text-sm font-medium underline underline-offset-4"
              href="/console"
            >
              Open console
            </Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Session pages</CardTitle>
            <CardDescription>
              Every published agent answers at /s/&lt;slug&gt;. A draft agent
              can be reached the same way via the console&apos;s Test call
              link.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              className="text-sm font-medium underline underline-offset-4"
              href="/console"
            >
              Find your agent&apos;s slug in the console
            </Link>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
