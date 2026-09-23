import * as React from "react";

import { Caveat, Patrick_Hand } from "next/font/google";

import { Toaster } from "@/components/ui/sonner";

/**
 * Dark session surface for `/s/[slug]` (docs/UI_UX_SPEC.md §2.2, §5).
 *
 * `.dark` + `data-surface="session"` fix the theme (the phone-call metaphor)
 * and set `color-scheme: dark`; the console's theme provider never reaches
 * here. The two handwriting faces the insurance notebook uses are loaded with
 * `next/font` and exposed as `--font-hand` / `--font-hand-label`, which is
 * what let the notebook drop its runtime Google Fonts `@import` (§2.3).
 *
 * The toaster lives here because the agent can ask the UI to show one over
 * the `lkap.ui.request` RPC (`method: "toast"`, CONTRACTS §10); on this
 * surface it is top-centre (§6).
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
      <Toaster theme="dark" position="top-center" richColors />
    </div>
  );
}
