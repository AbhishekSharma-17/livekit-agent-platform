import "server-only";

/**
 * Server-only helper for the session surface's test mode (DECISIONS-W2
 * D-W2-1). It reads `LKAP_ADMIN_TOKEN` directly — the same server env var the
 * console proxy (`src/app/api/console/[...path]/route.ts`) uses — so the
 * `import "server-only"` above must stay the first line: it makes this module
 * fail to bundle if anything client-side ever imports it, which is how we
 * guarantee the admin token never reaches the browser via this path.
 *
 * `web/src/lib/livekit.ts` stays the module client components import; nothing
 * in it may import this file.
 */
import type { AgentOut, AgentPublicOut } from "@/contracts/lkap-contracts";
import { errorFromPayload, publicApiBaseUrl, toPublicAgent } from "@/lib/livekit";

/**
 * `GET /v1/agents/{id_or_slug}` **with** the admin token, so a draft
 * (unpublished) agent's card can be rendered by `/s/[slug]?mode=test`.
 *
 * Server components only. Never called from client code, and never exposes
 * the raw `AgentOut` (which can reference credential ids) — only the mapped
 * `AgentPublicOut`.
 */
export async function fetchAdminAgentServerSide(
  slug: string,
): Promise<AgentPublicOut> {
  const response = await fetch(
    `${publicApiBaseUrl()}/v1/agents/${encodeURIComponent(slug)}`,
    {
      headers: { "X-Admin-Token": process.env.LKAP_ADMIN_TOKEN ?? "" },
      cache: "no-store",
    },
  );
  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) throw errorFromPayload(response.status, payload);
  return toPublicAgent(payload as AgentOut);
}
