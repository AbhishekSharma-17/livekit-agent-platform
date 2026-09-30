import { Suspense } from "react";
import type { Metadata } from "next";
import Link from "next/link";

import { StateMeter } from "@/components/shared/state-meter";

import { LoginForm } from "./login-form";
import { SignInCardSkeleton } from "./sign-in-card";
import { SignInShowcase } from "./sign-in-showcase";

export const metadata: Metadata = { title: "Sign in · LKAP console" };

/**
 * `/login` and `/login?invite=<token>` (V2-14, ask #37; CONTRACTS-V2 §3.1).
 * The sign-in archetype (docs/ui/DESIGN-SYSTEM.md section 7.4, docs/ui/AUDIT.md
 * D10): two columns, the 380 px form card on the left and a quiet showcase on
 * the right that hides at 1080 px and below. A server component so `metadata`
 * works; the form is the client `LoginForm`, which reads `useSearchParams`,
 * hence the `Suspense` boundary with a skeleton of the card as its fallback.
 */
export default function LoginPage() {
  return (
    <main className="grid min-h-dvh bg-background min-[1081px]:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col items-center justify-center gap-8 px-4 py-12">
        <Link
          href="/"
          aria-label="LKAP home"
          className="inline-flex items-center gap-2 rounded-sm text-body font-semibold tracking-[-0.008em] text-foreground"
        >
          <span aria-hidden="true" className="inline-flex">
            <StateMeter state="idle" size="sm" />
          </span>
          LKAP
        </Link>
        <Suspense fallback={<SignInCardSkeleton />}>
          <LoginForm />
        </Suspense>
      </div>
      <SignInShowcase />
    </main>
  );
}
