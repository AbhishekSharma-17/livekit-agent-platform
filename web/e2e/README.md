# web/e2e — Playwright tests

Run: `pnpm e2e` against a running app (`pnpm dev` on :3000, or `next start`;
`PLAYWRIGHT_BASE_URL` overrides the origin). Every spec is read-only against
the dev api.

- `preview.spec.ts` — every preview scene combination renders without a console error.
- `a11y.spec.ts` — no critical/serious axe finding on the console's static routes, `/login`, the session's unavailable page and the stage's reconnecting / embed scenes.
- `login-console-smoke.spec.ts` — needs `LKAP_E2E_EMAIL` / `LKAP_E2E_PASSWORD` and the admin bypass off; skipped otherwise.
- `text-chat.spec.ts` — `test.fixme` until a browser-facing fake worker exists (asks V2-18-13).
