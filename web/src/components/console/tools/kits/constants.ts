/**
 * `lkap_contracts.kits.KIT_PREFIX_PATTERN` — the generated contracts are types only (no
 * runtime values cross the `.d.ts` boundary), so the Add-kit dialog mirrors the pattern here,
 * the same way `tool-context.ts` mirrors the tool-context contract's own patterns.
 */
export const KIT_PREFIX_PATTERN = "^[a-z][a-z0-9_]{0,23}$";
