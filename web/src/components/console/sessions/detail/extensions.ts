import type { SessionDetailExtension } from "./types";
import { sessionsV2Extension } from "@/components/console/sessions-v2/extension";

/**
 * The session detail's plug-in list (WP-7 contract; rules in `../README.md`).
 *
 * This file is **append-only and shared**: a later package (V2-14 for
 * Recording, Cost and QA and the v2 timeline rows) adds one import of a
 * `SessionDetailExtension` exported from a module it owns, and one entry
 * below. It never edits the detail view, the built-in lists or another
 * package's entry. Order matters only for replacements (the last one wins)
 * and for concatenated slots (header actions appear in list order).
 */
export const SESSION_DETAIL_EXTENSIONS: SessionDetailExtension[] = [sessionsV2Extension];
