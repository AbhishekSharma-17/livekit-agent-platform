import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginForm } from "./login-form";
import { SignInCardSkeleton } from "./sign-in-card";
import { SignInShowcase } from "./sign-in-showcase";

export const metadata: Metadata = { title: "Sign in · LKAP console" };

/**
 * `/login` and `/login?invite=<token>` (V2-14, ask #37; CONTRACTS-V2 §3.1).
 * The sign-in archetype (docs/ui/DESIGN-SYSTEM.md section 7.4, docs/ui/AUDIT.md
 * D10): two columns, the 380 px form card on the left and an animated showcase on
 * the right that hides at 1080 px and below. The card carries the LKAP mark,
 * which links home. A server component so `metadata` works; the form is the
 * client `LoginForm`, which reads `useSearchParams`, hence the `Suspense`
 * boundary with a skeleton of the card as its fallback.
 */
export default function LoginPage() {
  return (
    <main className="grid min-h-dvh bg-background min-[1081px]:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col items-center justify-center px-4 py-12">
        <Suspense fallback={<SignInCardSkeleton />}>
          <LoginForm />
        </Suspense>
      </div>
      <SignInShowcase />
    </main>
  );
}
