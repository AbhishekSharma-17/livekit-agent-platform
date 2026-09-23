import type * as React from "react";
import type { LucideIcon } from "lucide-react";

import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * Section and slot contracts of the agent editor shell (WP-3). Later packages
 * plug in through `EditorExtension`s listed in `./extensions.ts`; see
 * `./README.md` for the rules.
 */

/** What a section's `visible` predicate can look at. */
export interface EditorSectionContext {
  agent: AgentOut;
  /** Live form value of the agent's mode (`AgentOut.mode`). */
  mode: "prompt" | "flow";
}

/** Props every section component receives. The form is reached with `useFormContext<AgentEditorForm>()`. */
export interface EditorSectionProps {
  agent: AgentOut;
}

export interface EditorSectionDef {
  /** The `?section=` value. Stable; never rename once shipped (links in issues and bookmarks use it). */
  id: string;
  /** Nav label, sentence case. */
  label: string;
  icon: LucideIcon;
  /** Position in the nav; built-ins use 10, 20, 30 … so an extension can slot between them. */
  order: number;
  Component: React.ComponentType<EditorSectionProps>;
  /** Hide the section (nav item and deep link) unless this returns true. Default: always visible. */
  visible?: (ctx: EditorSectionContext) => boolean;
  /**
   * Config-relative path prefixes whose validation issues belong to this
   * section (`"pipeline"` claims `pipeline.stt`, `pipeline.llm.model`, …).
   * Paths are `ValidationResult.issues[].path` values, or the leading token
   * of a plain `errors[]`/`warnings[]` string, or a form path with the
   * `config.` prefix removed. The longest matching prefix wins.
   */
  issuePaths?: string[];
  /** Fallback for issue strings that carry no path (the §7.14 keyword heuristic). */
  issueKeywords?: RegExp;
  /** Order in which keyword heuristics are tried (lower first). Default: `order`. */
  issueKeywordPriority?: number;
  /**
   * `"default"`: three columns (nav · content ≤ 720 px · summary rail).
   * `"full"`: the content takes the rail's column too (the flow canvas).
   */
  layout?: "default" | "full";
  /**
   * The shell renders the section's issue list above the section by default.
   * Set this when the section renders `<SectionIssueList />` itself (e.g. to
   * place it under its own intro).
   */
  ownsIssueList?: boolean;
}

/** A partial update of an already-registered section, matched by `id`. */
export type EditorSectionPatch = { id: string; patch: Partial<Omit<EditorSectionDef, "id">> };

/** Named places in the header and rail that a later package can fill or replace. */
export interface EditorSlots {
  /** Replaces the read-only connection chip in the header (V2-13: the change popover). */
  connectionChip?: React.ComponentType<{ agent: AgentOut }>;
  /** Replaces the read-only mode chip in the header (V2-16: the prompt ↔ flow switch dialog). */
  modeChip?: React.ComponentType<{ agent: AgentOut }>;
  /**
   * Extra items appended to the Test call menu (V2-18 "Test chat", V2-17
   * "Call a number"). Each renders `DropdownMenuItem`s; never open a Dialog
   * from inside the menu (render it as a sibling, controlled by state).
   */
  testCallItems?: React.ComponentType<{ agent: AgentOut; dirty: boolean }>[];
  /** Extra header actions left of Publish (V2-18 "Add to website"). */
  headerActions?: React.ComponentType<{ agent: AgentOut }>[];
  /** Renders the "History" affordance next to the version number in the rail (V2-16). */
  versionHistory?: React.ComponentType<{ agent: AgentOut }>;
}

export interface EditorExtension {
  /** Package id, for debugging ("V2-13"). */
  id: string;
  /** Full definitions add a section or replace the one with the same `id`. */
  sections?: EditorSectionDef[];
  /** Partial updates of existing sections (e.g. change a label or `visible`). Applied after `sections`. */
  sectionPatches?: EditorSectionPatch[];
  /** Single-component slots: the last extension wins. Array slots: concatenated in extension order. */
  slots?: EditorSlots;
}
