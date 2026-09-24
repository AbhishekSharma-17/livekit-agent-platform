"use client";

import * as React from "react";
import { ChevronsUpDownIcon, PencilLineIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { cn } from "@/lib/utils";
import type { ModelSpec } from "@/contracts/lkap-contracts";

export interface ModelComboboxProps {
  /** Forwarded to the trigger so a caller's `<label htmlFor>` / `Field` resolves. */
  id?: string;
  models: ModelSpec[];
  /** The stored model id; `""` means "use the provider default". */
  value: string;
  /** `ProviderSpec.default_model`; marked "Default" in the list. */
  defaultModel?: string | null;
  onChange: (value: string) => void;
  /** Controlled open state (the vision note opens the list pre-filtered). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Start with the list filtered to vision models (`supports_video`). */
  visionOnly?: boolean;
  disabled?: boolean;
  className?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

function matches(model: ModelSpec, needle: string): boolean {
  if (needle === "") return true;
  return model.label.toLowerCase().includes(needle) || model.id.toLowerCase().includes(needle);
}

/**
 * Model picker (docs/UI_UX_SPEC.md §4.4 step 3, §7.5 item 3): a `Command`
 * inside a `Popover`. Rows show the human label first, the id in mono and
 * the badges; the default model is marked. Free text always works — a
 * model outside the list is a validation warning, not an error
 * (CONTRACTS §6: LiveKit Inference's catalog churns) — through the
 * "Use "<query>" as a custom id" row.
 *
 * V2-06's vendor catalogs will feed extra `models` in; nothing here assumes
 * the list is complete.
 */
export function ModelCombobox({
  id,
  models,
  value,
  defaultModel,
  onChange,
  open: openProp,
  onOpenChange,
  visionOnly: visionOnlyProp = false,
  disabled,
  className,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
}: ModelComboboxProps) {
  const [openState, setOpenState] = React.useState(false);
  const open = openProp ?? openState;
  const [query, setQuery] = React.useState("");
  const [visionOnly, setVisionOnly] = React.useState(visionOnlyProp);
  const listId = React.useId();

  React.useEffect(() => {
    if (open) setVisionOnly(visionOnlyProp);
  }, [open, visionOnlyProp]);

  function setOpen(next: boolean) {
    if (!next) setQuery("");
    setOpenState(next);
    onOpenChange?.(next);
  }

  function choose(next: string) {
    onChange(next);
    setOpen(false);
  }

  const effective = value.trim() || defaultModel || "";
  const selected = models.find((m) => m.id === effective);
  const needle = query.trim().toLowerCase();
  const hasVision = models.some((m) => m.supports_video);
  const visible = models.filter((m) => matches(m, needle) && (!visionOnly || m.supports_video));
  const customId = query.trim();
  const offerCustom = customId !== "" && !models.some((m) => m.id === customId);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          disabled={disabled}
          className={cn("h-auto min-h-9 w-full justify-between gap-2 px-3 py-1.5 text-left font-normal", className)}
        >
          <ModelSummary model={selected} modelId={effective} isDefault={value.trim() === "" && Boolean(defaultModel)} />
          <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-72 p-0" aria-label="Choose a model">
        <Command shouldFilter={false} label="Models">
          <CommandInput value={query} onValueChange={setQuery} placeholder="Search models or type an id" />
          {hasVision ? (
            <div className="flex items-center gap-2 px-3 pt-2 text-xs text-muted-foreground">
              <button
                type="button"
                aria-pressed={visionOnly}
                onClick={() => setVisionOnly((v) => !v)}
                className={cn(
                  "rounded-xs px-1.5 py-0.5 font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  visionOnly ? "bg-brand-soft text-brand-text" : "bg-muted text-muted-foreground hover:text-foreground",
                )}
              >
                Vision only
              </button>
              {visionOnly ? <span>Showing models that can see images.</span> : null}
            </div>
          ) : null}
          <CommandList id={listId}>
            {!offerCustom ? <CommandEmpty>No models match.</CommandEmpty> : null}
            {visible.length > 0 ? (
              <CommandGroup heading="Suggested">
                {visible.map((model) => (
                  <CommandItem
                    key={model.id}
                    value={model.id}
                    onSelect={() => choose(model.id)}
                    data-checked={model.id === effective ? "true" : undefined}
                    className="items-start"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="text-sm text-foreground">{model.label}</span>
                        {model.id === defaultModel ? (
                          <span className="rounded-xs bg-muted px-1 text-[0.6875rem] leading-4 font-medium text-muted-foreground">
                            Default
                          </span>
                        ) : null}
                        {model.supports_video ? <CapabilityBadge kind="vision" /> : null}
                      </span>
                      <span className="truncate font-mono text-xs text-muted-foreground">{model.id}</span>
                      {model.note ? <span className="text-xs text-muted-foreground">{model.note}</span> : null}
                    </div>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}
            {offerCustom ? (
              <CommandGroup heading="Custom">
                <CommandItem value={`__custom__:${customId}`} onSelect={() => choose(customId)}>
                  <PencilLineIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0 truncate">
                    Use <span className="font-mono">&ldquo;{customId}&rdquo;</span> as a custom id
                  </span>
                </CommandItem>
              </CommandGroup>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** Label first, id second (mono); custom ids show "Custom model". */
export function ModelSummary({
  model,
  modelId,
  isDefault = false,
}: {
  model: ModelSpec | undefined;
  modelId: string;
  isDefault?: boolean;
}) {
  if (!modelId) {
    return <span className="text-sm text-muted-foreground">Choose a model</span>;
  }
  return (
    <span className="flex min-w-0 flex-col">
      <span className="flex items-center gap-1.5 truncate text-sm text-foreground">
        {model ? model.label : "Custom model"}
        {isDefault ? <span className="text-xs text-muted-foreground">· Default</span> : null}
      </span>
      <span className="truncate font-mono text-xs text-muted-foreground">{modelId}</span>
    </span>
  );
}
