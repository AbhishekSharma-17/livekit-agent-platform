/**
 * nwsapi 2.2.27 (jsdom's selector engine) answers `:fullscreen` and `:modal` by
 * calling `node.matches(...)` for the "native" state — which in jsdom *is*
 * nwsapi, so each check recurses until the stack overflows and the swallowed
 * RangeError reads as `false`. floating-ui's `isTopLayer` asks `:modal` for every
 * ancestor on each position update, so opening one Radix popover cost ~15 s of
 * CPU (≈39M `matches` calls) and dialog tests timed out under load.
 *
 * jsdom has no top layer and no fullscreen API, so both states are always
 * false here; answer that directly and leave every other selector untouched.
 */
const TOP_LAYER_STATES = new Set([":fullscreen", ":modal"]);

// Files that opt into `@vitest-environment node` have no DOM to patch.
if (typeof Element !== "undefined") {
  const nativeMatches = Element.prototype.matches;
  Element.prototype.matches = function matches(this: Element, selectors: string): boolean {
    return TOP_LAYER_STATES.has(selectors.trim()) ? false : nativeMatches.call(this, selectors);
  };
}
