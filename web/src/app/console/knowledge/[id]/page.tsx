import { KbDetail } from "@/components/console/knowledge/kb-detail";

export default async function KbDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <KbDetail kbId={id} />;
}
