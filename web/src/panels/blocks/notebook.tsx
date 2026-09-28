"use client";

/**
 * `notebook` block (V6-08 → V6-10 → V6-14, D-V6-15) — a sectioned notebook the agent
 * writes in as the call goes, and the caller may too: paper themes (`plain` /
 * `ruled` / `grid` / `legal`), a print or handwritten font, and up to
 * `MAX_NOTEBOOK_SECTIONS` sections of `text` / `checklist` / `details` / `ink`
 * (the drawing board; `notebook/sections.tsx`'s `InkSectionView` renders the section's
 * claimed `canvas` block, or "Drawing board coming soon" while it names none).
 *
 * `config.sections` orders the sections; each one's content comes from
 * `data.sections[<id>]` when present and of the matching kind, else its own
 * empty content (`notebook/sections.ts::sectionContent` — never trust
 * `blockStateOf`'s shallow merge for this nested map, ask #56).
 * `caller_can_write` is the single switch for every caller affordance inside
 * (`notebook/sections.tsx`, the shared `useCallerEdit` hook).
 *
 * Loaded lazily by `<Block>` (`blocks/index.tsx`): the paper CSS and the four
 * section renderers stay out of the first load for a panel with no notebook.
 */
import * as React from "react";
import { useId } from "react";

import type { NotebookBlockState } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import {
  ChecklistSectionView,
  DetailsSectionView,
  InkSectionView,
  TextSectionView,
  sectionContent,
  type NotebookSectionConfigLike,
  type NotebookSectionKind,
} from "./notebook/sections";
import { NOTEBOOK_PAPER_CSS } from "./notebook/paper-theme";
import type { BlockRenderProps } from "./types";

const SECTION_KINDS: ReadonlySet<string> = new Set<NotebookSectionKind>(["text", "checklist", "details", "ink"]);

function isSectionKind(value: unknown): value is NotebookSectionKind {
  return typeof value === "string" && SECTION_KINDS.has(value);
}

/** `config.sections`, defaulted the way `NotebookBlockConfig.sections` itself defaults (one `notes` text section). */
function sectionsOf(config: unknown): NotebookSectionConfigLike[] {
  const record = typeof config === "object" && config !== null ? (config as Record<string, unknown>) : {};
  const raw = record.sections;
  const list = Array.isArray(raw) ? raw : [];
  const sections: NotebookSectionConfigLike[] = [];
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const { id, title, kind, canvas_block_id: canvasBlockId } = entry as Record<string, unknown>;
    if (typeof id !== "string") continue;
    sections.push({
      id,
      title: typeof title === "string" ? title : "",
      kind: isSectionKind(kind) ? kind : "text",
      canvasBlockId: typeof canvasBlockId === "string" ? canvasBlockId : null,
    });
  }
  return sections.length > 0 ? sections : [{ id: "notes", title: "Notes", kind: "text", canvasBlockId: null }];
}

function paperOf(config: unknown): string {
  const value = (config as { paper?: unknown } | null)?.paper;
  return typeof value === "string" && ["plain", "ruled", "grid", "legal"].includes(value) ? value : "ruled";
}

function fontOf(config: unknown): "print" | "handwritten" {
  const value = (config as { font?: unknown } | null)?.font;
  return value === "handwritten" ? "handwritten" : "print";
}

function sectionHeading(section: NotebookSectionConfigLike): string {
  if (section.title.trim()) return section.title;
  switch (section.kind) {
    case "checklist":
      return "Checklist";
    case "details":
      return "Summary";
    case "ink":
      return "Sketch";
    default:
      return "Notes";
  }
}

type NotebookBlockRenderProps = BlockRenderProps<NotebookBlockState>;

export function NotebookBlock({ spec, data, panel, title, highlighted }: NotebookBlockRenderProps) {
  const styleId = useId();
  const sections = sectionsOf(spec.config);
  const callerCanWrite = (spec.config as { caller_can_write?: unknown } | null)?.caller_can_write === true;
  const callerCanDraw = (spec.config as { caller_can_draw?: unknown } | null)?.caller_can_draw === true;
  const paper = paperOf(spec.config);
  const font = fontOf(spec.config);

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {/* `notebook-styles.ts` does the same: a vitest-safe injected stylesheet, not a `.css` import. */}
      <style id={`lkap-notebook-paper-css-${styleId}`}>{NOTEBOOK_PAPER_CSS}</style>
      {sections.length === 0 ? (
        <PanelEmpty>This notebook has no sections yet.</PanelEmpty>
      ) : (
        <div
          data-slot="notebook"
          className="lkap-notebook-paper @container flex flex-col gap-5"
          data-paper={paper}
          data-font={font}
        >
          {sections.map((section) => (
            <section key={section.id} data-slot="notebook-section" data-section-id={section.id} data-section-kind={section.kind}>
              <h4 className="lkap-notebook-heading mb-2 text-sm font-semibold">{sectionHeading(section)}</h4>
              {section.kind === "text" && (
                <TextSectionView
                  blockId={spec.id}
                  section={section}
                  content={sectionContent(data.sections, section) as never}
                  callerCanWrite={callerCanWrite}
                  perform={panel.perform}
                />
              )}
              {section.kind === "checklist" && (
                <ChecklistSectionView
                  blockId={spec.id}
                  section={section}
                  content={sectionContent(data.sections, section) as never}
                  callerCanWrite={callerCanWrite}
                  perform={panel.perform}
                />
              )}
              {section.kind === "details" && (
                <DetailsSectionView
                  blockId={spec.id}
                  section={section}
                  content={sectionContent(data.sections, section) as never}
                  callerCanWrite={callerCanWrite}
                  perform={panel.perform}
                />
              )}
              {section.kind === "ink" && (
                <InkSectionView canvasBlockId={section.canvasBlockId} callerCanDraw={callerCanDraw} panel={panel} />
              )}
            </section>
          ))}
        </div>
      )}
    </BlockFrame>
  );
}

export default NotebookBlock;
