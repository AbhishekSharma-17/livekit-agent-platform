"use client";

/**
 * The board's toolbar (V6-14): pen / highlighter / eraser / box / arrow (whichever
 * `config.tools` offers, `text` always excluded — a stroke never carries text, D-V6-16), a
 * small colour swatch, undo (erases the caller's own last stroke) and clear. Every control
 * is a plain `<button>` — keyboard-operable and screen-reader-labelled — even though
 * freehand drawing itself has no keyboard equivalent (§ "keyboard alternatives where
 * feasible": there is no feasible one for hand-drawing, so the surrounding controls carry
 * the accessibility weight instead).
 */
import type { LucideIcon } from "lucide-react";
import { ArrowUpRight, Eraser, Highlighter, Pencil, Square, Trash2, Undo2 } from "lucide-react";
import * as React from "react";

import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { CanvasToolChoice } from "./permissions";

const TOOL_LABEL: Record<CanvasToolChoice, string> = {
  pen: "Pen",
  highlighter: "Highlighter",
  eraser: "Eraser",
  box: "Box",
  arrow: "Arrow",
};

const TOOL_ICON: Record<CanvasToolChoice, LucideIcon> = {
  pen: Pencil,
  highlighter: Highlighter,
  eraser: Eraser,
  box: Square,
  arrow: ArrowUpRight,
};

/** A small, high-contrast palette — five colours plus the caller's current one is plenty for a call. */
export const CANVAS_COLOR_SWATCHES: readonly string[] = ["#1f2937", "#dc2626", "#2563eb", "#16a34a", "#f59e0b"];

export interface CanvasToolbarProps {
  tools: readonly CanvasToolChoice[];
  tool: CanvasToolChoice;
  onToolChange: (tool: CanvasToolChoice) => void;
  color: string;
  onColorChange: (color: string) => void;
  onUndo: () => void;
  canUndo: boolean;
  onClear: () => void;
  canClear: boolean;
}

export function CanvasToolbar({ tools, tool, onToolChange, color, onColorChange, onUndo, canUndo, onClear, canClear }: CanvasToolbarProps) {
  const showColors = tool !== "eraser";
  return (
    <div data-slot="canvas-toolbar" role="toolbar" aria-label="Drawing tools" className="border-border bg-muted/20 flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
      {tools.map((t) => {
        const ToolIcon = TOOL_ICON[t];
        return (
          <Button
            key={t}
            type="button"
            variant={tool === t ? "secondary" : "ghost"}
            size="icon-sm"
            aria-pressed={tool === t}
            aria-label={TOOL_LABEL[t]}
            onClick={() => onToolChange(t)}
          >
            <Icon as={ToolIcon} size="sm" />
          </Button>
        );
      })}
      {showColors && (
        <div role="group" aria-label="Colour" className="ml-1 flex items-center gap-1">
          {CANVAS_COLOR_SWATCHES.map((swatch) => (
            <button
              key={swatch}
              type="button"
              aria-label={`Colour ${swatch}`}
              aria-pressed={color === swatch}
              onClick={() => onColorChange(swatch)}
              className={cn(
                "size-5 rounded-full border transition-transform focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                color === swatch ? "border-foreground scale-110" : "border-border/60",
              )}
              style={{ backgroundColor: swatch }}
            />
          ))}
        </div>
      )}
      <div className="ml-auto flex items-center gap-1">
        <Button type="button" variant="ghost" size="icon-sm" aria-label="Undo last stroke" disabled={!canUndo} onClick={onUndo}>
          <Icon as={Undo2} size="sm" />
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" aria-label="Clear the board" disabled={!canClear} onClick={onClear}>
          <Icon as={Trash2} size="sm" />
        </Button>
      </div>
    </div>
  );
}
