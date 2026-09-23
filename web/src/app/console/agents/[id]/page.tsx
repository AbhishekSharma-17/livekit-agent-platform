import { Suspense } from "react";
import type { Metadata } from "next";

import { AgentEditor } from "@/components/console/agents/agent-editor";

export const metadata: Metadata = { title: "Agent" };

/**
 * `/console/agents/[id]?section=…` (docs/UI_UX_SPEC.md §3.5). The editor reads
 * `?section=` with `useSearchParams`, hence the Suspense boundary.
 */
export default async function AgentEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <AgentEditor agentId={id} />
    </Suspense>
  );
}
