/**
 * The claim notebook's stylesheet, as a string injected by the panel.
 *
 * It is not a `.css` import on purpose: the web package's PostCSS config uses
 * Next's string plugin entries, which Vite (and therefore vitest) cannot load,
 * so importing a stylesheet from a component under test breaks `pnpm test`.
 * Injecting one <style> element keeps the panel self-contained instead.
 *
 * The rules are a port of the Gemini demo's `styles.css` — ruled paper, ink-in
 * note lines, red blanks, taped polaroids, the rubber stamp, the scribbling
 * pen — scoped under `.lkap-notebook` so nothing leaks into the session shell.
 *
 * The two handwriting faces are **not** loaded here (docs/UI_UX_SPEC.md §2.3):
 * the runtime Google Fonts `@import` is gone, and the paper reads the CSS
 * variables `--font-hand` (Caveat) and `--font-hand-label` (Patrick Hand) that
 * the surface around it defines with `next/font/google` — the session layout
 * (WP-8) and the preview layout (WP-10). `next/font` cannot be called from a
 * panel module itself, because `panels/registry.ts` makes it a transitive
 * import of every session-surface test and the loader only runs under Next's
 * compiler; the `var(…, "Caveat", …)` fallbacks keep the paper legible
 * wherever the variables are not set (tests, other surfaces).
 *
 * Only the three signature animations live here; they are switched off under
 * `prefers-reduced-motion`.
 */
export const NOTEBOOK_CSS = `
.lkap-notebook {
  --paper: #fbf6ea;
  --paper-line: #dcd2bd;
  --paper-margin: #e3b1ad;
  --ink: #23211c;
  --ink-soft: #5d574b;
  --ink-urgent: #b1372d;
  --ink-ok: #3f7a3a;
  --ink-info: #2f6a94;
  --tape: rgba(236, 222, 176, 0.85);
  --hand: var(--font-hand, "Caveat"), ui-rounded, cursive;
  --hand-label: var(--font-hand-label, "Patrick Hand"), ui-rounded, cursive;
  --marker-x: -42px;
}

/* the paper */
.lkap-notebook .paper {
  position: relative;
  background: var(--paper);
  color: var(--ink);
  border-radius: 6px 12px 12px 6px;
  padding: 26px 32px 108px 76px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45), 0 2px 0 rgba(255, 255, 255, 0.5) inset;
  background-image:
    linear-gradient(90deg, transparent 52px, var(--paper-margin) 52px, var(--paper-margin) 54px, transparent 54px),
    repeating-linear-gradient(transparent 0 33px, var(--paper-line) 33px 34px);
  background-position: 0 0, 0 86px;
}

.lkap-notebook .holes {
  position: absolute;
  left: 14px;
  top: 70px;
  bottom: 70px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  pointer-events: none;
}

.lkap-notebook .holes span {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--ink) 78%, transparent);
  box-shadow: inset 0 2px 3px rgba(0, 0, 0, 0.55);
}

.lkap-notebook .page-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 16px;
  font-family: var(--hand-label);
  font-size: 16px;
  color: var(--ink-soft);
  margin-bottom: 22px;
}

.lkap-notebook .page-head .page-title { color: var(--ink); }

/* the handwriting */
.lkap-notebook .notes {
  font-family: var(--hand);
  font-size: 27px;
  line-height: 34px;
  min-height: 34px;
  list-style: none;
  padding: 0;
  margin: 0;
}

.lkap-notebook .note {
  position: relative;
  padding-right: 8px;
  overflow-wrap: anywhere;
}

.lkap-notebook .note.title { font-size: 32px; font-weight: 600; }
.lkap-notebook .note.aside { font-size: 22px; color: var(--ink-soft); }
.lkap-notebook .note.blank { color: var(--ink-urgent); }

.lkap-notebook .note.blank .blank-line {
  display: inline-block;
  width: 150px;
  height: 0;
  border-bottom: 2px solid var(--ink-urgent);
  vertical-align: -6px;
  margin-left: 8px;
}

.lkap-notebook .note.urgent {
  color: var(--ink-urgent);
  text-decoration: underline;
  text-decoration-thickness: 2px;
  text-underline-offset: 5px;
}

.lkap-notebook .note.check { color: var(--ink-ok); }

/* the hand-drawn tick in the margin */
.lkap-notebook .note.check::before {
  content: "";
  position: absolute;
  left: var(--marker-x);
  top: 8px;
  width: 9px;
  height: 17px;
  border: solid var(--ink-ok);
  border-width: 0 3px 3px 0;
  transform: rotate(40deg);
}

/* the exclamation mark in the margin */
.lkap-notebook .note.flag::before {
  content: "!";
  position: absolute;
  left: calc(var(--marker-x) + 2px);
  top: -2px;
  font-family: var(--hand);
  font-size: 34px;
  font-weight: 600;
  color: var(--ink-urgent);
}

.lkap-notebook .note.ink-in {
  animation: lkapInkIn 720ms cubic-bezier(0.2, 0.7, 0.3, 1) both;
}

/* the clip rect is inflated so the margin markers survive the end state */
@keyframes lkapInkIn {
  from { clip-path: inset(-16px calc(100% + 80px) -10px -80px); opacity: 0.4; }
  to { clip-path: inset(-16px 0 -10px -80px); opacity: 1; }
}

/* the pinboard */
.lkap-notebook .board {
  display: flex;
  flex-wrap: wrap;
  gap: 30px 26px;
  margin-top: 30px;
  align-items: flex-start;
}

.lkap-notebook .frame {
  position: relative;
  width: 220px;
  margin: 0;
  background: #fff;
  padding: 8px 8px 16px;
  border: 1px solid #d9d0bb;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.16);
  transform: rotate(var(--tilt, -2deg));
  animation: lkapPinIn 520ms cubic-bezier(0.2, 0.8, 0.3, 1.2) both;
}

/* the strip of tape across the top */
.lkap-notebook .frame::before {
  content: "";
  position: absolute;
  top: -10px;
  left: 50%;
  width: 74px;
  height: 20px;
  margin-left: -37px;
  background: var(--tape);
  transform: rotate(-3deg);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
}

.lkap-notebook .frame.sketch { width: 280px; }

.lkap-notebook .frame img {
  display: block;
  width: 100%;
  height: auto;
  background: #e6e1d6;
}

.lkap-notebook .frame .missing {
  display: grid;
  place-items: center;
  aspect-ratio: 4 / 3;
  background: color-mix(in srgb, var(--ink) 8%, #e6e1d6);
  font-family: var(--hand-label);
  font-size: 14px;
  color: var(--ink-soft);
  text-align: center;
  padding: 10px;
}

.lkap-notebook .frame figcaption {
  font-family: var(--hand);
  font-size: 19px;
  line-height: 21px;
  color: var(--ink);
  margin-top: 8px;
}

.lkap-notebook .frame .tag {
  display: block;
  font-family: var(--hand-label);
  font-size: 13px;
  line-height: 1.35;
  color: var(--ink-soft);
  margin-top: 3px;
}

.lkap-notebook .frame .tag.unconfirmed { color: var(--ink-urgent); }
.lkap-notebook .frame.sketch figcaption { color: var(--ink-info); }

@keyframes lkapPinIn {
  from { opacity: 0; transform: rotate(var(--tilt, -2deg)) translateY(18px) scale(0.96); }
  to { opacity: 1; transform: rotate(var(--tilt, -2deg)) translateY(0) scale(1); }
}

/* the stamp and the pen */
.lkap-notebook .stamp {
  position: absolute;
  right: 34px;
  bottom: 38px;
  padding: 6px 16px;
  font-family: var(--hand-label);
  font-size: 30px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-urgent);
  border: 4px solid var(--ink-urgent);
  border-radius: 6px;
  transform: rotate(-9deg);
  opacity: 0.85;
  mix-blend-mode: multiply;
  animation: lkapStampIn 420ms cubic-bezier(0.2, 0.9, 0.3, 1.4) both;
}

.lkap-notebook .stamp[data-tone="success"] { color: var(--ink-ok); border-color: var(--ink-ok); }
.lkap-notebook .stamp[data-tone="info"] { color: var(--ink-info); border-color: var(--ink-info); }
.lkap-notebook .stamp[data-tone="neutral"] { color: var(--ink-soft); border-color: var(--ink-soft); }

@keyframes lkapStampIn {
  from { opacity: 0; transform: rotate(-9deg) scale(1.6); }
  to { opacity: 0.85; transform: rotate(-9deg) scale(1); }
}

.lkap-notebook .pen {
  position: absolute;
  left: 76px;
  bottom: 42px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--hand);
  font-size: 22px;
  color: var(--ink-soft);
}

.lkap-notebook .pen-nib {
  width: 14px;
  height: 14px;
  border-radius: 50% 50% 50% 0;
  background: var(--ink);
  transform: rotate(-45deg);
  animation: lkapScribble 0.9s ease-in-out infinite;
}

@keyframes lkapScribble {
  0%, 100% { transform: rotate(-45deg) translate(0, 0); }
  50% { transform: rotate(-45deg) translate(3px, -2px); }
}

/* the claim details */
.lkap-notebook .sections > summary {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  list-style: none;
  font-family: var(--hand-label);
  font-size: 16px;
  color: var(--ink-soft);
  padding: 3px 2px;
  border-radius: 6px;
}

.lkap-notebook .sections > summary::-webkit-details-marker { display: none; }

.lkap-notebook .sections > summary::before {
  content: "";
  width: 6px;
  height: 6px;
  border-right: 2px solid var(--ink-soft);
  border-bottom: 2px solid var(--ink-soft);
  transform: rotate(-45deg);
  transition: transform 160ms ease;
}

.lkap-notebook .sections[open] > summary::before { transform: rotate(45deg); }

.lkap-notebook .sections > summary:focus-visible {
  outline: 2px solid var(--ink-info);
  outline-offset: 2px;
}

/*
 * The claim details themselves are the platform's details block now (V5-12)
 * — its own tokens apply inside .sections, not the paper's ink palette; no
 * .field / .f-* rules are needed here any more.
 */

/* the % ready ring */
.lkap-notebook .ring {
  --value: 0;
  position: relative;
  width: 46px;
  height: 46px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: conic-gradient(var(--ring-tone, currentColor) calc(var(--value) * 1%), color-mix(in srgb, var(--muted-foreground, currentColor) 28%, transparent) 0);
  display: grid;
  place-items: center;
  transition: background 320ms ease;
}

.lkap-notebook .ring::after {
  content: "";
  position: absolute;
  inset: 5px;
  border-radius: 50%;
  /*
   * The disc is the card surface behind the ring. It reads --card and not
   * Tailwind's --color-card: @theme inline emits the latter on :root with the
   * light value only, so it stays pale on the dark session surface.
   */
  background: var(--card, var(--color-card));
}

.lkap-notebook .ring > span {
  position: relative;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

/* narrow panel column */
@container (max-width: 640px) {
  .lkap-notebook { --marker-x: -28px; }

  /*
   * Bottom padding = the scaled stamp's own height (~50px) + its offset from
   * the edge (24px) + a line of clearance, so the stamp never lands on the
   * last handwritten line on a 358px-wide phone column.
   */
  .lkap-notebook .paper {
    padding: 20px 16px 106px 44px;
    background-image:
      linear-gradient(90deg, transparent 28px, var(--paper-margin) 28px, var(--paper-margin) 30px, transparent 30px),
      repeating-linear-gradient(transparent 0 30px, var(--paper-line) 30px 31px);
    background-position: 0 0, 0 74px;
  }

  .lkap-notebook .holes { display: none; }
  .lkap-notebook .notes { font-size: 24px; line-height: 31px; }
  .lkap-notebook .note.title { font-size: 27px; }
  .lkap-notebook .note.aside { font-size: 20px; }
  .lkap-notebook .frame, .lkap-notebook .frame.sketch { width: 100%; }
  .lkap-notebook .stamp {
    right: 16px;
    bottom: 24px;
    font-size: 22px;
    max-width: calc(100% - 32px);
    padding: 4px 12px;
  }
  .lkap-notebook .pen { left: 44px; bottom: 28px; font-size: 18px; }
}

@media (prefers-reduced-motion: reduce) {
  .lkap-notebook .note.ink-in,
  .lkap-notebook .frame,
  .lkap-notebook .stamp,
  .lkap-notebook .pen-nib {
    animation: none;
  }
}
`;
