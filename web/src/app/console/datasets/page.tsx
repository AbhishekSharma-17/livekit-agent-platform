import type { Metadata } from "next";

import { DatasetList } from "@/components/console/datasets/dataset-list";

export const metadata: Metadata = { title: "Lookup tables" };

export default function DatasetsPage() {
  return <DatasetList />;
}
