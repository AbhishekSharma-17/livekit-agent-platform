"use client";

import { Section, SectionRow } from "@/components/shared/section";
import { RequireWrite } from "@/components/shared/require-write";
import { VendorName } from "@/components/shared/vendor-mark";

import { AgentActivityTable } from "./agent-activity-table";
import { AgentKeysTable } from "./agent-keys-table";
import { ConnectAgentDialog } from "./connect-agent-dialog";
import { AGENT_KEY_CLIENTS } from "./snippets";
import { useActiveWorkspace, useInvalidateSettings } from "./use-settings-queries";

/**
 * Settings → AI agents (docs/v3/AGENT-ACCESS.md §5, PLAN-V3 V3-04). Reading
 * `GET /v1/api-keys` and `GET /v1/audit` both need `admin` server-side
 * regardless of API-key scopes (`auth/deps.py::require` — a *user* session
 * only ever satisfies the role branch), so the whole tab is gated the same
 * way `WebhooksTab` gates itself: no point mounting a query that only 403s.
 */
export function AiAgentsTab() {
  return (
    <RequireWrite min="admin" title="Only admins and owners can connect an AI agent">
      <AiAgentsTabInner />
    </RequireWrite>
  );
}

function AiAgentsTabInner() {
  const { workspace } = useActiveWorkspace();
  const invalidate = useInvalidateSettings();

  return (
    <div className="flex flex-col gap-4">
      <Section
        id="connect-agent"
        title="Connect an AI agent"
        description="Give Claude Code, Codex or any MCP-capable coding agent a scoped key to build and test on this workspace."
        aside={<ConnectAgentDialog onCreated={() => invalidate(workspace?.id)} />}
      >
        <SectionRow>
          <p className="max-w-[72ch] text-label text-text-secondary">
            Every agent key is scoped, expires and can be revoked. The agent never sees your admin sign-in, and every
            change it makes is attributed and listed below.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-label text-text-secondary">
            <span>Works with</span>
            <ul data-slot="agent-clients" className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-foreground">
              {AGENT_KEY_CLIENTS.map((client) => (
                <li key={client.id}>
                  <VendorName vendor={client.mark}>{client.id === "other" ? "Any MCP client" : client.label}</VendorName>
                </li>
              ))}
            </ul>
          </div>
        </SectionRow>
      </Section>

      <Section id="agent-keys" title="Agent keys" description="Keys created for AI coding agents.">
        <SectionRow>
          <AgentKeysTable />
        </SectionRow>
      </Section>

      <Section id="agent-activity" title="Agent activity" description="Changes an AI agent made through the MCP server.">
        <SectionRow>
          <AgentActivityTable />
        </SectionRow>
      </Section>
    </div>
  );
}
