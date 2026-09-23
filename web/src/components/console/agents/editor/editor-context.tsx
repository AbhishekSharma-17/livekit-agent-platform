"use client";

import * as React from "react";

import type { AgentOut } from "@/contracts/lkap-contracts";

import type { EditorSectionDef } from "./types";
import { GENERAL_SECTION, normalizeIssuePath, type EditorIssue } from "./validation-map";

/**
 * Shell state that sections may read (WP-3). Every consumer must tolerate a
 * missing provider: section components are rendered by their own tests
 * without the shell, so `useSectionIssues` returns an empty result there.
 */
export interface EditorContextValue {
  agent: AgentOut;
  /** Visible sections in nav order. */
  sections: EditorSectionDef[];
  activeSection: string;
  /** Switches section (updates `?section=` with `router.replace`). */
  goToSection: (id: string) => void;
  /** Server (last validate / failed save) and client (zod) issues, already mapped to sections. */
  issues: EditorIssue[];
  /** Switches to the issue's section and focuses its field when one can be found. */
  focusIssue: (issue: EditorIssue) => void;
}

const EditorContext = React.createContext<EditorContextValue | null>(null);

export const EditorContextProvider = EditorContext.Provider;

/** The shell's context, or null outside the editor. */
export function useEditorContext(): EditorContextValue | null {
  return React.useContext(EditorContext);
}

export interface SectionIssues {
  /** Issues mapped to this section. */
  issues: EditorIssue[];
  errors: EditorIssue[];
  warnings: EditorIssue[];
  /** Issues no section claimed; the shell lists them in every section. */
  general: EditorIssue[];
  /** First issue whose path is `path` or below it (`issueFor("pipeline.stt")`), for field-level hints. */
  issueFor: (path: string) => EditorIssue | undefined;
  focusIssue: (issue: EditorIssue) => void;
}

const NOOP = () => {};

/**
 * Issues for one section (default: the active one). Safe outside the shell:
 * returns empty lists and a no-op `focusIssue`.
 */
export function useSectionIssues(sectionId?: string): SectionIssues {
  const ctx = useEditorContext();
  return React.useMemo(() => {
    const id = sectionId ?? ctx?.activeSection;
    const all = ctx?.issues ?? [];
    const issues = all.filter((issue) => issue.section === id);
    return {
      issues,
      errors: issues.filter((issue) => issue.severity === "error"),
      warnings: issues.filter((issue) => issue.severity === "warning"),
      general: all.filter((issue) => issue.section === GENERAL_SECTION),
      issueFor: (path: string) => {
        const target = normalizeIssuePath(path);
        return all.find(
          (issue) => issue.path !== null && (issue.path === target || issue.path.startsWith(`${target}.`)),
        );
      },
      focusIssue: ctx?.focusIssue ?? NOOP,
    };
  }, [ctx, sectionId]);
}
