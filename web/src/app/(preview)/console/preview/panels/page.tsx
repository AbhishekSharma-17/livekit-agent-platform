import type { Metadata } from "next";

import { PreviewShell } from "@/components/preview/preview-shell";
import { PreviewSceneRenderer } from "@/components/preview/preview-scene-renderer";
import {
  SCENES,
  resolveSceneParams,
  sceneListPayload,
  type Surface,
} from "@/components/preview/scenes";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * Query-driven scenes (docs/UI_UX_SPEC.md §7.11 item 2): `?scene=<id>&...`.
 * `?scene=list` is the self-describing entry the capture script reads first —
 * see `src/components/preview/README.md` for the full contract.
 */
export default async function PreviewPanelsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const raw = await searchParams;
  const sceneId = first(raw.scene) ?? "session";

  if (sceneId === "list") {
    return (
      <pre data-testid="scene-list" className="p-4 text-xs whitespace-pre-wrap">
        {JSON.stringify(sceneListPayload(), null, 2)}
      </pre>
    );
  }

  const scene = SCENES[sceneId];
  const flat: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(raw)) flat[key] = first(value);
  const params = resolveSceneParams(sceneId, flat);
  const surface: Surface = first(raw.surface) === "light" ? "light" : first(raw.surface) === "dark" ? "dark" : (scene?.surfaces[0] ?? "light");

  if (!scene) {
    return (
      <PreviewShell sceneId={sceneId} params={{}} surface={surface}>
        <div className="p-6 text-sm">
          <p className="text-danger-text">
            No scene named &ldquo;{sceneId}&rdquo;. Known scenes:{" "}
            {Object.keys(SCENES).join(", ")}, or <code>list</code>.
          </p>
        </div>
      </PreviewShell>
    );
  }

  return (
    <PreviewShell sceneId={sceneId} params={params} surface={surface}>
      <PreviewSceneRenderer sceneId={sceneId} params={params} />
    </PreviewShell>
  );
}
