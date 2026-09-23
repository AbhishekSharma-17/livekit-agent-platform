/**
 * WP-0 shared primitives (docs/UI_UX_SPEC.md §2.7). Contract + usage notes:
 * `./README.md`. Per-file imports (`@/components/shared/state-meter`) are
 * preferred in the session bundle; this barrel is a convenience for console code.
 */
export * from "./agent-state";
export * from "./capability-badge";
export * from "./copy-button";
export * from "./description-list";
export * from "./empty-state";
export * from "./field";
export * from "./gated-button";
export * from "./icon";
export * from "./kbd";
export * from "./new-resource-button";
export * from "./page-header";
export * from "./require-write";
export * from "./relative-time";
export * from "./responsive-table";
export * from "./section";
export * from "./state-meter";
export * from "./status-chip";
export * from "./vendor-mark";
