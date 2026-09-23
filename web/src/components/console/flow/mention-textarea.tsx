"use client";

import * as React from "react";

import { Textarea } from "@/components/ui/textarea";
import type { VariableSpec } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import { insertMention, mentionQuery } from "./flow-model";

/**
 * A textarea with `@mention` insertion of flow variables (V2-16): typing `@`
 * opens a list of the flow's variables filtered by what follows; Enter/Tab or a
 * click inserts `{{ name }}` — the placeholder syntax the worker renders
 * (`lkap_agent.flow.variables.render_template`). Arrow keys move, Esc closes.
 */
export interface MentionTextareaProps
  extends Omit<React.ComponentProps<"textarea">, "value" | "onChange"> {
  value: string;
  onValueChange: (next: string) => void;
  variables: readonly VariableSpec[];
}

export function MentionTextarea({ value, onValueChange, variables, className, id, ...props }: MentionTextareaProps) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = React.useState<string | null>(null);
  const [active, setActive] = React.useState(0);
  const listId = `${id ?? "mention"}-variables`;

  const matches = React.useMemo(
    () => (query === null ? [] : variables.filter((variable) => variable.name.startsWith(query))),
    [query, variables],
  );
  const open = query !== null && matches.length > 0;

  function refresh(text: string, caret: number) {
    const found = mentionQuery(text, caret);
    setQuery(found ? found.query : null);
    setActive(0);
  }

  function choose(name: string) {
    const element = ref.current;
    const caret = element?.selectionStart ?? value.length;
    const next = insertMention(value, caret, name);
    onValueChange(next.text);
    setQuery(null);
    window.requestAnimationFrame(() => {
      if (!element) return;
      element.focus();
      element.setSelectionRange(next.caret, next.caret);
    });
  }

  return (
    <div className="relative">
      <Textarea
        {...props}
        id={id}
        ref={ref}
        value={value}
        className={cn("min-h-24 resize-y", className)}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        onChange={(event) => {
          onValueChange(event.target.value);
          refresh(event.target.value, event.target.selectionStart ?? event.target.value.length);
        }}
        onKeyDown={(event) => {
          if (!open) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((index) => (index + 1) % matches.length);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((index) => (index - 1 + matches.length) % matches.length);
          } else if (event.key === "Enter" || event.key === "Tab") {
            event.preventDefault();
            choose(matches[active].name);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            setQuery(null);
          }
        }}
        onBlur={() => window.setTimeout(() => setQuery(null), 120)}
      />
      {open ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="Flow variables"
          className="absolute inset-x-0 top-full z-50 mt-1 max-h-48 overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-md"
        >
          {matches.map((variable, index) => (
            <li
              key={variable.name}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={cn(
                "flex cursor-pointer items-baseline justify-between gap-2 rounded-sm px-2 py-1 text-sm",
                index === active ? "bg-muted text-foreground" : "text-muted-foreground",
              )}
              onMouseDown={(event) => {
                event.preventDefault();
                choose(variable.name);
              }}
            >
              <span className="font-mono text-xs text-foreground">{`{{ ${variable.name} }}`}</span>
              <span className="truncate text-xs">{variable.description || variable.type}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
