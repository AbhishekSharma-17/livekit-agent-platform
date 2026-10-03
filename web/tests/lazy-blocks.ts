/**
 * Preloads the panel blocks that `src/panels/blocks/index.tsx` loads with `React.lazy`
 * (and the link block's QR encoder), so a test that renders them does not pay for the
 * first transform of each module inside its own timeout.
 *
 * Why. In vitest a dynamic `import()` is served by the Vite dev server on first use,
 * which transforms the file and its imports. A composite panel over the fixture layout
 * holds one block of every type, so the first test that renders one starts about
 * sixteen of those at once. On a quiet machine that takes a second or two. On a loaded one
 * it passes the 1 s default of Testing Library's `findBy*`, and sometimes the worker's
 * own `fetch` call to the server times out (`Timeout calling "fetch" with
 * "/src/panels/blocks/link.tsx"`). Awaiting this in a `beforeAll` moves that cost out of
 * the test, and afterwards every `React.lazy` resolves from the module cache.
 *
 * Use it with a hook timeout of its own, never a global one:
 *
 *   beforeAll(async () => { await preloadLazyBlocks(); }, PRELOAD_TIMEOUT_MS);
 */

/** A generous ceiling for the one-off transform work, applied to the hook only. */
export const PRELOAD_TIMEOUT_MS = 90_000;

/** Every lazily loaded block module, plus the link block's QR encoder. */
export function preloadLazyBlocks(): Promise<unknown[]> {
  return Promise.all([
    import("@/panels/blocks/activity"),
    import("@/panels/blocks/document"),
    import("@/panels/blocks/table"),
    import("@/panels/blocks/video"),
    import("@/panels/blocks/markdown"),
    import("@/panels/blocks/upload"),
    import("@/panels/blocks/captions"),
    import("@/panels/blocks/transcript"),
    import("@/panels/blocks/notebook"),
    import("@/panels/blocks/layout"),
    import("@/panels/blocks/canvas"),
    import("@/panels/blocks/signature"),
    import("@/panels/blocks/chart"),
    import("@/panels/blocks/timer"),
    import("@/panels/blocks/code"),
    import("@/panels/blocks/cart"),
    import("qrcode-generator"),
  ]);
}
