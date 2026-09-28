# web/tests — vitest unit/component tests

Run: `pnpm test` (vitest, jsdom, `passWithNoTests: true` until Wave 1 adds
specs). Wave 1 adds `ui-state.test.ts` (reducer), `registry.test.ts`,
`generic-panel.test.tsx`, `console-*.test.tsx`; Wave 2 adds
`insurance-notebook.test.tsx`. Fixtures land under `web/tests/fixtures/`.
