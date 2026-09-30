import { Suspense } from "react";
import type { Metadata } from "next";

import { AgentEditor, EditorSkeleton } from "@/components/console/agents/agent-editor";

export const metadata: Metadata = { title: "Agent" };

/**
 * `/console/agents/[id]?section=…` (docs/UI_UX_SPEC.md §3.5). The editor reads
 * `?section=` with `useSearchParams`, hence the Suspense boundary; its
 * fallback is the editor's own skeleton, never a blank area
 * (docs/ui/DESIGN-SYSTEM.md section 8.1).
 */
export default async function AgentEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense fallback={<EditorSkeleton />}>
      <AgentEditor agentId={id} />
    </Suspense>
  );
}
