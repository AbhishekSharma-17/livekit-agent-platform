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
import {
  SessionLoadError,
  SessionSkeleton,
  type SessionSkeletonChannel,
} from "@/components/session/session-skeleton";
import type { ConnectErrorKind } from "@/lib/livekit";

/**
 * One `next/dynamic` wrapper per channel over the **same** import (one shared
 * chunk): `loading` receives no props, and each channel's skeleton mirrors its
 * own layout (the text chat, or the voice pre-call card). A chunk that fails
 * to load gets the error state with a Reload, never a blank frame. The loaders
 * stay inline arrows so Next's `next/dynamic` transform can see the import.
 */
function embedFallback(channel: SessionSkeletonChannel) {
  function EmbedFallback({ error }: { error?: Error | null }) {
    return error ? <SessionLoadError /> : <SessionSkeleton channel={channel} />;
  }
  return EmbedFallback;
}

const TextEmbedSession = dynamic(() => import("@/components/session/embed/embed-session"), {
  ssr: false,
  loading: embedFallback("text"),
});

const VoiceEmbedSession = dynamic(() => import("@/components/session/embed/embed-session"), {
  ssr: false,
  loading: embedFallback("voice"),
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
  return props.channel === "text" ? <TextEmbedSession {...props} /> : <VoiceEmbedSession {...props} />;
}
