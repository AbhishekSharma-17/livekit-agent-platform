/**
 * The preview route's own chrome (docs/UI_UX_SPEC.md §7.11 item 1: "no
 * sidebar (a minimal top bar with a state selector)"). Server-renderable —
 * every control is a plain link that rewrites the query string, so switching
 * scenes needs no client JS and the route stays crawlable by nothing (see
 * `robots` on the layout).
 */
import type { ReactNode } from "react";
import Link from "next/link";

import { SCENES, type Surface } from "./scenes";

export interface PreviewShellProps {
  sceneId: string;
  params: Record<string, string>;
  surface: Surface;
  children: ReactNode;
}

function hrefFor(sceneId: string, params: Record<string, string>, surface: Surface): string {
  const search = new URLSearchParams({ scene: sceneId, ...params, surface });
  return `/console/preview/panels?${search.toString()}`;
}

export function PreviewShell({ sceneId, params, surface, children }: PreviewShellProps) {
  const scene = SCENES[sceneId];
  const isDark = surface === "dark";

  return (
    <div className={isDark ? "dark" : undefined}>
      <div
        data-testid="preview-shell"
        data-surface={scene?.isSessionSurface ? "session" : undefined}
        className="bg-background text-foreground min-h-dvh"
      >
        <div
          data-testid="preview-top-bar"
          role="region"
          aria-label="Preview controls"
          className="border-border bg-card sticky top-0 z-50 flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2 text-sm"
        >
          <span className="text-muted-foreground font-medium">Preview</span>
          <nav aria-label="Scenes" className="flex flex-wrap gap-2">
            {Object.values(SCENES).map((entry) => (
              <Link
                key={entry.id}
                href={hrefFor(entry.id, entry.defaults, entry.surfaces[0])}
                aria-current={entry.id === sceneId ? "page" : undefined}
                className={
                  entry.id === sceneId
                    ? "text-brand-text underline underline-offset-4"
                    : "text-muted-foreground hover:text-foreground"
                }
              >
                {entry.label}
              </Link>
            ))}
          </nav>
          <span className="flex-1" />
          {scene && (
            <form action="/console/preview/panels" className="flex flex-wrap items-center gap-2">
              <input type="hidden" name="scene" value={sceneId} />
              {Object.keys(scene.params).map((key) => (
                <label key={key} className="text-muted-foreground flex items-center gap-1">
                  {key}
                  <select
                    name={key}
                    defaultValue={params[key]}
                    className="border-input bg-background rounded-sm border px-1 py-0.5 text-xs"
                  >
                    {scene.params[key].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <label className="text-muted-foreground flex items-center gap-1">
                surface
                <select
                  name="surface"
                  defaultValue={surface}
                  className="border-input bg-background rounded-sm border px-1 py-0.5 text-xs"
                >
                  <option value="dark">dark</option>
                  <option value="light">light</option>
                </select>
              </label>
              <button
                type="submit"
                className="border-border hover:bg-muted rounded-sm border px-2 py-0.5 text-xs"
              >
                Go
              </button>
            </form>
          )}
        </div>
        {/* Scenes built on `SessionCardScreen` (precall/ended/unavailable) render
            their own `<main>` — a second one here would be
            `landmark-no-duplicate-main` / `landmark-main-is-top-level`. */}
        {scene?.ownsMain ? children : <main>{children}</main>}
      </div>
    </div>
  );
}
