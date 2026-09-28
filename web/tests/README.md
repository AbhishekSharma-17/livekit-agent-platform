# web/tests — vitest unit/component tests

Run: `pnpm test` (vitest, jsdom, `passWithNoTests: true` until Wave 1 adds
specs). Wave 1 adds `ui-state.test.ts` (reducer), `registry.test.ts`,
`generic-panel.test.tsx`, `console-*.test.tsx`; V6-22 replaced Wave 2's
insurance notebook test with `insurance-notebook-alias.test.tsx`. Fixtures land under `web/tests/fixtures/`.
