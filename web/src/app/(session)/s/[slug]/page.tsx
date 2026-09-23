import * as React from "react";

import type { Metadata } from "next";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { SessionExperience } from "@/components/session/session-experience";
import {
  classifyConnectError,
  describeConnectError,
  fetchPublicAgent,
  type ConnectErrorKind,
} from "@/lib/livekit";
import { fetchAdminAgentServerSide } from "@/lib/livekit-server";

import { EmbedLayout } from "./embed-layout";

/**
 * Public session page (docs/ARCHITECTURE.md §12, CONTRACTS §7).
 *
 * The agent card is fetched server-side from the **public** endpoint
 * `GET /v1/agents/{slug}` (published agents only, no token). Everything else
 * — the connect call and the LiveKit room — happens in the browser against the
 * same public API. No admin token is ever involved on this surface.
 *
 * DECISIONS-W2 D-W2-1: `?mode=test` is the one exception. It fetches the
 * agent card via the server-only admin proxy helper (`fetchAdminAgentServerSide`,
 * `import "server-only"`), so a draft/unpublished agent can be previewed from
 * the console — and threads `testMode` down so the browser's connect call
 * also goes through `/api/console/*` instead of the public API. The admin
 * token itself never leaves the server: it is read from `LKAP_ADMIN_TOKEN` in
 * `livekit-server.ts` / the console proxy route, never passed as a prop.
 */
export const dynamic = "force-dynamic";

interface SessionPageProps {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ mode?: string; embed?: string; channel?: string }>;
}

async function loadAgent(
  slug: string,
  testMode: boolean,
): Promise<{
  agent: AgentPublicOut | null;
  loadError: string | null;
  loadErrorKind: ConnectErrorKind | null;
}> {
  try {
    const agent = testMode
      ? await fetchAdminAgentServerSide(slug)
      : await fetchPublicAgent(slug);
    return { agent, loadError: null, loadErrorKind: null };
  } catch (cause) {
    // UI_UX_SPEC §5.7: the kind picks the "unavailable" page. In test mode the
    // lookup goes through the admin proxy, so a 403 is a token problem rather
    // than "this agent is a draft".
    return {
      agent: null,
      loadError: describeConnectError(cause),
      loadErrorKind: classifyConnectError(cause, { viaConsole: testMode }),
    };
  }
}

export async function generateMetadata({
  params,
  searchParams,
}: SessionPageProps): Promise<Metadata> {
  const { slug } = await params;
  const { mode } = await searchParams;
  const { agent } = await loadAgent(slug, mode === "test");
  return {
    title: agent ? `${agent.name} — live session` : "Live session",
    description: agent?.description || undefined,
  };
}

export default async function SessionPage({
  params,
  searchParams,
}: SessionPageProps) {
  const { slug } = await params;
  const { mode, embed, channel } = await searchParams;
  const testMode = mode === "test";
  const { agent, loadError, loadErrorKind } = await loadAgent(slug, testMode);

  // `?embed=1` (V2-18, UI_UX_SPEC-V2-AMENDMENTS §2.6): the widget's iframe and
  // the console's Test chat dialog preview. `middleware.ts` sets this route's
  // `Content-Security-Policy: frame-ancestors` header for the same request —
  // that is the actual origin enforcement; this branch only picks the layout.
  if (embed === "1") {
    return (
      <EmbedLayout
        slug={slug}
        agent={agent}
        loadError={loadError}
        loadErrorKind={loadErrorKind}
        channel={channel === "text" ? "text" : "voice"}
        testMode={testMode}
      />
    );
  }

  return (
    <SessionExperience
      slug={slug}
      agent={agent}
      loadError={loadError}
      loadErrorKind={loadErrorKind}
      testMode={testMode}
      privacyUrl={process.env.NEXT_PUBLIC_LKAP_PRIVACY_URL || undefined}
    />
  );
}
