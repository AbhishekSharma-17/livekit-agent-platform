/**
 * The `notebook` block's paper: four backgrounds (`plain` / `ruled` / `grid` /
 * `legal`) and two writing themes (`print` / `handwritten`) — V6-10 (D-V6-15).
 *
 * A CSS string injected by the block itself, the same reason
 * `insurance_notebook/notebook-styles.ts` is one and not a `.css` import: the
 * web package's PostCSS config uses Next's string-plugin entries, which Vite
 * (and so `pnpm test`) cannot load. Scoped under `.lkap-notebook-paper` so
 * nothing leaks into the session shell; a fixed cream/ink palette on purpose,
 * in both light and dark shell themes — real paper does not follow the
 * console's theme, exactly like the insurance pack's own notebook.
 *
 * `--font-hand` is the same variable `insurance_notebook` reads
 * (`next/font/google` sets it on the session/preview layout); the `cursive`
 * fallback keeps the handwritten theme legible wherever it is unset (tests,
 * other surfaces).
 */
export const NOTEBOOK_PAPER_CSS = `
.lkap-notebook-paper {
  --paper: #fbf6ea;
  --paper-line: #dcd2bd;
  --paper-margin: #e3b1ad;
  --ink: #23211c;
  --ink-soft: #5d574b;
  --hand: var(--font-hand, "Caveat"), ui-rounded, cursive;
  position: relative;
  background: var(--paper);
  color: var(--ink);
  border-radius: 6px 12px 12px 6px;
  padding: 22px 24px;
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.5) inset, 0 10px 24px rgba(0, 0, 0, 0.12);
}

.lkap-notebook-paper[data-paper="plain"] {
  background-image: none;
}

.lkap-notebook-paper[data-paper="ruled"] {
  padding-left: 44px;
  background-image:
    linear-gradient(90deg, transparent 30px, var(--paper-margin) 30px, var(--paper-margin) 32px, transparent 32px),
    repeating-linear-gradient(transparent 0 27px, var(--paper-line) 27px 28px);
  background-position: 0 0, 0 40px;
}

.lkap-notebook-paper[data-paper="grid"] {
  background-image:
    repeating-linear-gradient(0deg, transparent 0 23px, var(--paper-line) 23px 24px),
    repeating-linear-gradient(90deg, transparent 0 23px, var(--paper-line) 23px 24px);
}

.lkap-notebook-paper[data-paper="legal"] {
  --paper: #fdf6c9;
  --paper-line: #c9c19a;
  padding-left: 44px;
  background-image:
    linear-gradient(90deg, transparent 30px, var(--paper-margin) 30px, var(--paper-margin) 32px, transparent 32px),
    repeating-linear-gradient(transparent 0 30px, var(--paper-line) 30px 31px);
  background-position: 0 0, 0 44px;
}

.lkap-notebook-paper[data-font="handwritten"] {
  font-family: var(--hand);
  font-size: 22px;
  line-height: 1.35;
}

.lkap-notebook-paper[data-font="handwritten"] .lkap-notebook-heading {
  font-family: inherit;
  font-size: 1.15em;
  font-weight: 600;
}

.lkap-notebook-paper .lkap-notebook-caller-mark {
  font-family: system-ui, sans-serif;
  font-size: 0.6em;
  vertical-align: middle;
}

@keyframes lkapNotebookInkIn {
  from { clip-path: inset(-8px 100% -8px -40px); opacity: 0.3; }
  to { clip-path: inset(-8px 0 -8px -40px); opacity: 1; }
}

.lkap-notebook-paper .lkap-notebook-ink-in {
  animation: lkapNotebookInkIn 480ms cubic-bezier(0.2, 0.7, 0.3, 1) both;
}

@container (max-width: 400px) {
  .lkap-notebook-paper {
    padding: 16px 16px 16px 32px;
  }
  .lkap-notebook-paper[data-paper="ruled"],
  .lkap-notebook-paper[data-paper="legal"] {
    padding-left: 32px;
    background-position: 0 0, 0 34px;
  }
  .lkap-notebook-paper[data-font="handwritten"] {
    font-size: 19px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .lkap-notebook-paper .lkap-notebook-ink-in {
    animation: none;
  }
}
`;
