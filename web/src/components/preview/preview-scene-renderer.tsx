"use client";

/**
 * The client boundary for scene content (docs/UI_UX_SPEC.md §7.11).
 *
 * `page.tsx` is a server component (it reads `searchParams`); the scenes
 * themselves pass closures (`onStart`, `perform`, `onRetry`, ...) into `"use
 * client"` leaves (`StageView`, `PreCallCard`, `EndOfCallCard`, panel
 * components) — React cannot serialize a plain function across the
 * server/client boundary, so those closures must be *created* on the client
 * side of that boundary, not passed through it. This component is that
 * boundary: `page.tsx` hands it only serializable data (`sceneId`, a flat
 * string-keyed `params` object), and every closure `scenes.tsx` builds is
 * created here, inside the client tree.
 */
import { SCENES } from "./scenes";

export function PreviewSceneRenderer({
  sceneId,
  params,
}: {
  sceneId: string;
  params: Record<string, string>;
}) {
  const scene = SCENES[sceneId];
  if (!scene) return null;
  return <>{scene.render(params)}</>;
}
