import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in · LKAP console" };

/**
 * `/login` and `/login?invite=<token>` (V2-14, ask #37; CONTRACTS-V2 §3.1,
 * docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1 "Console-styled auth card; no
 * marketing"). A server component only so `metadata` works; everything else
 * lives in the client `LoginForm` (it reads `useSearchParams`, hence the
 * `Suspense` boundary — matches `console/providers/page.tsx`'s pattern).
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
