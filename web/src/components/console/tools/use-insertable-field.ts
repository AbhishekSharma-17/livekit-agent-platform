"use client";

import * as React from "react";

/**
 * Insert a token at an `<input>`/`<textarea>`'s cursor (the `mention-textarea.tsx` pattern:
 * a DOM ref read at insert time — its `selectionStart`/`selectionEnd` survive the field
 * losing focus to a menu — then a `requestAnimationFrame` refocus). `caret` re-renders on
 * every select/click/key so a caller can gray out "Insert value" while the cursor sits
 * somewhere a placeholder may not go (V6-11's URL host guard), without waiting for a click.
 */
export function useInsertableField<T extends HTMLInputElement | HTMLTextAreaElement>(
  value: string,
  onChange: (next: string) => void,
) {
  const ref = React.useRef<T | null>(null);
  const [caret, setCaret] = React.useState(value.length);

  function trackCaret(event: React.SyntheticEvent<T>) {
    setCaret(event.currentTarget.selectionStart ?? value.length);
  }

  function insert(token: string) {
    const element = ref.current;
    const start = element?.selectionStart ?? caret;
    const end = element?.selectionEnd ?? start;
    const next = value.slice(0, start) + token + value.slice(end);
    onChange(next);
    const nextCaret = start + token.length;
    setCaret(nextCaret);
    window.requestAnimationFrame(() => {
      if (!ref.current) return;
      ref.current.focus();
      ref.current.setSelectionRange(nextCaret, nextCaret);
    });
  }

  return { ref, caret, trackCaret, insert };
}
