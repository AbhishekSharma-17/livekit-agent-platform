import { AgentEditor } from "@/components/console/agents/agent-editor";

export default async function AgentEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AgentEditor agentId={id} />;
}
