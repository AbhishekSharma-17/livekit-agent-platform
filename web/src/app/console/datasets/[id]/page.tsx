import { DatasetDetail } from "@/components/console/datasets/dataset-detail";

export default async function DatasetDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DatasetDetail datasetId={id} />;
}
