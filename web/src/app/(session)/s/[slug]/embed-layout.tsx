"use client";

/**
 * `/s/[slug]?embed=1` entry point (V2-18). A thin client boundary so
 * `components/session/embed/embed-session.tsx` — the LiveKit room, the text
 * chat UI, the `postMessage` bridge — loads as its own on-demand chunk
 * (`next/dynamic(..., { ssr: false })`), not as part of the normal session
 * route's initial bundle. `page.tsx` statically imports *this* file (cheap:
 * a dynamic-import wrapper plus a loading fallback) and only renders it when
 * `embed=1`, so a non-embed visit to `/s/[slug]` never even requests the
 * embed chunk.
 *
 * R-V2-18 (PLAN-V2 §8): `/s/[slug]` First Load JS budget is 620 kB with
 * ~30 kB reserved for this; keeping the embed session tree behind
 * `next/dynamic` is what makes that budget achievable — a static import here
 * would put the whole embed UI (and its `@livekit/components-react` chat
 * hooks) in every visitor's first load, embedded or not.
 */
import dynamic from "next/dynamic";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import type { ConnectErrorKind } from "@/lib/livekit";

const EmbedSession = dynamic(() => import("@/components/session/embed/embed-session"), {
  ssr: false,
  loading: () => <div className="h-dvh w-full" aria-hidden="true" />,
});

export interface EmbedLayoutProps {
  slug: string;
  agent: AgentPublicOut | null;
  loadError: string | null;
  loadErrorKind: ConnectErrorKind | null;
  channel: "text" | "voice";
  testMode: boolean;
}

export function EmbedLayout(props: EmbedLayoutProps) {
  return <EmbedSession {...props} />;
}
