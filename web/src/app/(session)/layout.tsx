import * as React from "react";

import { Toaster } from "@/components/ui/sonner";

/**
 * Dark session-surface theme for `/s/[slug]` (docs/ARCHITECTURE.md §12).
 * The toaster lives here because the agent can ask the UI to show one over
 * the `lkap.ui.request` RPC (`method: "toast"`, CONTRACTS §10).
 */
export default function SessionLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="dark bg-background text-foreground min-h-dvh">
      {children}
      <Toaster theme="dark" position="top-center" richColors />
    </div>
  );
}
