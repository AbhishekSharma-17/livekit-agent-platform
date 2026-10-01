# web/e2e — Playwright tests

Run: `pnpm e2e` against a running app (`pnpm dev` on :3000, or `next start`;
`PLAYWRIGHT_BASE_URL` overrides the origin). Every spec is read-only against
the dev api.

- `preview.spec.ts` — every preview scene combination renders without a console error.
- `render-check.spec.ts` — every route in `docs/ui/AUDIT.md` section 2 (plus each settings tab, the styleguide and the caller page's pre-call, test and embed views), light and dark, at 390, 768 and 1440 px; fails on console errors and on sideways scroll at 390 px. Screenshots and `render-report.md` go to `RENDER_CHECK_OUT` (default `test-results/render-check`). Navigation only; detail routes use the first id the dev api lists. Every console route runs as owner, builder and viewer through the development-only view-as switch (`src/components/console/lib/dev-view-as.ts`, set via its `localStorage` key), which needs `next dev` with the admin bypass; without it the builder and viewer runs are reported as unreachable. It also checks that `/console/settings?tab=knowledge-connections` lands on `/console/knowledge?tab=connections`. Run it with `--workers=2` on a dev server.
- `a11y.spec.ts` — no critical/serious axe finding on the console's static routes, `/login`, the session's unavailable page and the stage's reconnecting / embed scenes.
- `login-console-smoke.spec.ts` — needs `LKAP_E2E_EMAIL` / `LKAP_E2E_PASSWORD` and the admin bypass off; skipped otherwise.
- `text-chat.spec.ts` — `test.fixme` until a browser-facing fake worker exists (asks V2-18-13).
