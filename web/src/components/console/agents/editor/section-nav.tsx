"use client";

import * as React from "react";

import { Icon } from "@/components/shared/icon";
import { cn } from "@/lib/utils";

import type { EditorSectionDef } from "./types";
import { sectionTone, type SectionIssueSummary } from "./validation-map";

export interface SectionNavProps {
  sections: EditorSectionDef[];
  active: string;
  onSelect: (id: string) => void;
  summary: Record<string, SectionIssueSummary>;
  /** `list`: vertical (≥ 1024 px). `bar`: horizontally scrollable segmented control (< 1024 px). */
  variant: "list" | "bar";
  className?: string;
}

/**
 * Section navigation (docs/UI_UX_SPEC.md §4.3): icon + label + validation dot
 * (danger for errors, warning for warnings); the active item uses the
 * brand-soft surface. Roving focus: arrow keys (and Home/End) move focus,
 * Enter/Space switch; switching is instant (no animation).
 */
export function SectionNav({ sections, active, onSelect, summary, variant, className }: SectionNavProps) {
  const listRef = React.useRef<HTMLUListElement>(null);
  const [focusId, setFocusId] = React.useState(active);
  const tabStop = sections.some((section) => section.id === focusId) ? focusId : active;

  function moveFocus(from: number, delta: number | "first" | "last") {
    const count = sections.length;
    if (count === 0) return;
    const index = delta === "first" ? 0 : delta === "last" ? count - 1 : (from + delta + count) % count;
    const id = sections[index].id;
    setFocusId(id);
    listRef.current?.querySelector<HTMLButtonElement>(`[data-section-id="${id}"]`)?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = variant === "list" ? ["ArrowDown", "ArrowRight"] : ["ArrowRight", "ArrowDown"];
    const prev = variant === "list" ? ["ArrowUp", "ArrowLeft"] : ["ArrowLeft", "ArrowUp"];
    if (next.includes(event.key)) moveFocus(index, 1);
    else if (prev.includes(event.key)) moveFocus(index, -1);
    else if (event.key === "Home") moveFocus(index, "first");
    else if (event.key === "End") moveFocus(index, "last");
    else return;
    event.preventDefault();
  }

  const isBar = variant === "bar";

  // Keep the active item visible in the horizontally scrolling bar (deep links, issue jumps).
  React.useEffect(() => {
    if (!isBar) return;
    const button = listRef.current?.querySelector<HTMLElement>(`[data-section-id="${active}"]`);
    const scroller = listRef.current?.closest<HTMLElement>(".overflow-x-auto");
    if (!button || !scroller) return;
    const left = button.getBoundingClientRect().left - scroller.getBoundingClientRect().left + scroller.scrollLeft;
    const right = left + button.offsetWidth;
    if (left < scroller.scrollLeft) scroller.scrollLeft = Math.max(0, left - 16);
    else if (right > scroller.scrollLeft + scroller.clientWidth) scroller.scrollLeft = right - scroller.clientWidth + 16;
  }, [active, isBar]);

  return (
    <nav aria-label="Agent sections" data-variant={variant} className={className}>
      <ul
        ref={listRef}
        className={cn(
          isBar
            ? "flex w-max min-w-full gap-1 rounded-md bg-muted p-1"
            : "flex flex-col gap-0.5",
        )}
      >
        {sections.map((section, index) => {
          const selected = section.id === active;
          const tone = sectionTone(summary[section.id]);
          return (
            <li key={section.id} className={cn(isBar && "shrink-0")}>
              <button
                type="button"
                data-section-id={section.id}
                aria-current={selected ? "page" : undefined}
                tabIndex={section.id === tabStop ? 0 : -1}
                onClick={() => {
                  setFocusId(section.id);
                  onSelect(section.id);
                }}
                onKeyDown={(event) => onKeyDown(event, index)}
                onFocus={() => setFocusId(section.id)}
                className={cn(
                  "group/nav-item flex w-full items-center gap-2.5 rounded-sm text-left text-sm font-medium outline-none",
                  "transition-colors duration-(--dur-2) ease-out",
                  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                  isBar ? "h-8 px-3 whitespace-nowrap" : "h-9 px-2.5",
                  selected
                    ? "bg-brand-soft text-brand-text"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Icon
                  as={section.icon}
                  size={isBar ? "md" : "lg"}
                  className={cn(selected ? "text-brand-text" : "text-muted-foreground group-hover/nav-item:text-foreground")}
                />
                <span className={cn(!isBar && "min-w-0 flex-1 truncate")}>{section.label}</span>
                {tone ? (
                  <span
                    data-tone={tone}
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      tone === "error" ? "bg-danger" : "bg-warning",
                    )}
                  >
                    <span className="sr-only">{tone === "error" ? "Has errors" : "Has warnings"}</span>
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
