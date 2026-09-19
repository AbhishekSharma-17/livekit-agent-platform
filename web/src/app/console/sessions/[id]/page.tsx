import { SessionDetailView } from "@/components/console/sessions/session-detail-view";

export default async function SessionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SessionDetailView sessionId={id} />;
}
