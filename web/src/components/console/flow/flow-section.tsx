"use client";

import dynamic from "next/dynamic";

import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import { Skeleton } from "@/components/ui/skeleton";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * The agent editor's `flow` section (V2-16; visible only in flow mode, full
 * width). The canvas — `@xyflow/react`, its stylesheet and dagre — is a
 * separate chunk loaded on demand here, so neither the editor's first load nor
 * the `/s/[slug]` session bundle carries it (PLAN-V2 V2-16, R-V2-18).
 */
const FlowCanvas = dynamic(() => import("./flow-canvas").then((mod) => mod.FlowCanvas), {
  ssr: false,
  loading: () => (
    <div aria-busy="true" aria-label="Loading the flow canvas" className="flex flex-col gap-3">
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-[520px] w-full" />
    </div>
  ),
});

export function FlowSection({ agent }: { agent: AgentOut }) {
  const editor = useEditorContext();
  return (
    <div className="flex flex-col gap-3" data-slot="flow-section">
      <p className="text-sm text-muted-foreground">
        Each step runs with the agent&apos;s{" "}
        <button
          type="button"
          className="font-medium text-foreground underline underline-offset-2"
          onClick={() => editor?.goToSection("instructions")}
        >
          base instructions (apply to every node)
        </button>
        , then the global rules, then the step&apos;s own instructions. Click a node or path to edit it; drag
        from a node&apos;s bottom handle to connect it.
      </p>
      <FlowCanvas agent={agent} />
    </div>
  );
}
