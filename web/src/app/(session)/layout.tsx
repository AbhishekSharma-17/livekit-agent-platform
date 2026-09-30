import * as React from "react";

import { Caveat, Patrick_Hand } from "next/font/google";

import { Toaster } from "@/components/ui/sonner";

/**
 * The caller-facing session surface for `/s/[slug]` (and its `?embed=1`
 * widget), a documented exception to the console design system (decision D3,
 * docs/ui/AUDIT.md): it is **always dark** (the phone-call metaphor), its body
 * text is **16 px** (`text-base`), and the transcript is a **bottom sheet** on
 * phones. Everything else (tokens, icons, primitives, states and copy) follows
 * docs/ui/DESIGN-SYSTEM.md.
 *
 * Dark is forced in two places. The root layout's `ThemeProvider` does reach
 * this route: `forcedThemeForPath()` (`lib/theme.ts`) forces next-themes to
 * `dark` for every `/s/*` path without touching the person's stored choice.
 * The `.dark` + `data-surface="session"` wrapper below is the server-rendered
 * guarantee: the tokens and `color-scheme: dark` apply on first paint, before
 * next-themes runs.
 *
 * The two handwriting faces the insurance notebook uses are loaded with
 * `next/font` and exposed as `--font-hand` / `--font-hand-label`, which is
 * what let the notebook drop its runtime Google Fonts `@import`.
 *
 * The toaster lives here because the agent can ask the UI to show one over
 * the `lkap.ui.request` RPC (`method: "toast"`, CONTRACTS §10). It is the app's
 * one toast primitive with its default policy (bottom-right; success and info
 * dismiss after 6 s; errors and warnings stay until dismissed), not a second
 * colour system.
 */
const caveat = Caveat({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-hand",
});

const patrickHand = Patrick_Hand({
  subsets: ["latin"],
  weight: "400",
  display: "swap",
  variable: "--font-hand-label",
});

export default function SessionLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      data-surface="session"
      className={`dark bg-background text-foreground min-h-dvh text-base ${caveat.variable} ${patrickHand.variable}`}
    >
      {children}
      <Toaster theme="dark" />
    </div>
  );
}
