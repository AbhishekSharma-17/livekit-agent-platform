"use client"

import * as React from "react"
import { cn } from "cn"
import { ChevronsUpDownIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

export interface SearchableSelectOption {
  value: string
  label: string
  icon?: React.ReactNode
  /** Shown right-aligned on the row, e.g. an app count for a category. */
  count?: number
  disabled?: boolean
}

export interface SearchableSelectGroup {
  /** Omit for an ungrouped, unlabelled section. */
  heading?: string
  options: SearchableSelectOption[]
}

export interface SearchableSelectProps {
  /** Forwarded to the trigger so a caller's `<label htmlFor>` / `Field` resolves. */
  id?: string
  /** A flat list, or `groups` for headed sections — pass exactly one. */
  options?: SearchableSelectOption[]
  groups?: SearchableSelectGroup[]
  /** A leading, unfiltered option (e.g. "All categories") pinned above every group. */
  allOption?: Pick<SearchableSelectOption, "value" | "label" | "count">
  /** `null`/`""` reads as "nothing picked" (shows `placeholder`, or `allOption` when given). */
  value: string | null
  onValueChange: (value: string) => void
  /** Multi-select instead: ignores `value`/`onValueChange`. Selecting toggles membership; the popover stays open. */
  multiple?: boolean
  values?: string[]
  onValuesChange?: (values: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  emptyText?: string
  /** Shown in place of the list while the options are still loading. */
  loading?: boolean
  loadingText?: string
  disabled?: boolean
  triggerClassName?: string
  contentClassName?: string
  "aria-label"?: string
  "aria-describedby"?: string
  "aria-invalid"?: boolean
  "aria-required"?: boolean
}

function normalize(text: string): string {
  return text.trim().toLowerCase()
}

function matches(option: SearchableSelectOption, needle: string): boolean {
  if (needle === "") return true
  return normalize(option.label).includes(needle) || normalize(option.value).includes(needle)
}

function filterGroups(groups: SearchableSelectGroup[], needle: string): SearchableSelectGroup[] {
  if (needle === "") return groups
  return groups
    .map((group) => ({ ...group, options: group.options.filter((option) => matches(option, needle)) }))
    .filter((group) => group.options.length > 0)
}

function CountBadge({ count }: { count: number }) {
  return <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{count}</span>
}

function OptionRow({ option }: { option: SearchableSelectOption }) {
  return (
    <>
      {option.icon ? <span className="flex size-4 shrink-0 items-center justify-center">{option.icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{option.label}</span>
      {option.count != null ? <CountBadge count={option.count} /> : null}
    </>
  )
}

/**
 * A searchable dropdown for a list too long to scan at a glance (a category
 * filter, an id picker) — `Popover` + `Command` (cmdk) in place of a native
 * `<select>` or the plain shadcn `Select`, so typing narrows the list instead
 * of scrolling it. Keyboard navigation, an optional pinned "All …" row,
 * per-option icon/count and headed groups all come from the one component;
 * the list scrolls inside the popover so it stays usable at phone width.
 */
export function SearchableSelect({
  id,
  options,
  groups: groupsProp,
  allOption,
  value,
  onValueChange,
  multiple = false,
  values = [],
  onValuesChange,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  emptyText = "Nothing matches.",
  loading = false,
  loadingText = "Loading…",
  disabled = false,
  triggerClassName,
  contentClassName,
  "aria-label": ariaLabel,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
  "aria-required": required,
}: SearchableSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const listId = React.useId()

  const groups = React.useMemo<SearchableSelectGroup[]>(
    () => groupsProp ?? (options ? [{ options }] : []),
    [groupsProp, options],
  )
  const allOptions = React.useMemo(() => groups.flatMap((group) => group.options), [groups])

  function setOpenState(next: boolean) {
    if (!next) setQuery("")
    setOpen(next)
  }

  function isChosen(optionValue: string): boolean {
    return multiple ? values.includes(optionValue) : optionValue === (value ?? "")
  }

  function choose(optionValue: string) {
    if (multiple) {
      const next = values.includes(optionValue)
        ? values.filter((v) => v !== optionValue)
        : [...values, optionValue]
      onValuesChange?.(next)
      return
    }
    onValueChange(optionValue)
    setOpenState(false)
  }

  const needle = normalize(query)
  const visibleGroups = filterGroups(groups, needle)
  // The "All …" row is pinned above every group and stays visible no matter
  // what is typed — it is not itself a value to search for, it resets the
  // filter. Only the option groups below it count toward "nothing matches".
  const nothingVisible = visibleGroups.every((g) => g.options.length === 0)

  const triggerContent = React.useMemo(() => {
    if (multiple) {
      if (values.length === 0) return { label: allOption?.label ?? placeholder, icon: undefined as React.ReactNode }
      if (values.length === 1) {
        const found = allOptions.find((o) => o.value === values[0])
        return { label: found?.label ?? values[0], icon: found?.icon }
      }
      return { label: `${values.length} selected`, icon: undefined as React.ReactNode }
    }
    if (!value || (allOption && value === allOption.value)) {
      if (allOption) return { label: allOption.label, icon: undefined as React.ReactNode }
      return { label: value || placeholder, icon: undefined as React.ReactNode }
    }
    const found = allOptions.find((o) => o.value === value)
    return { label: found?.label ?? value, icon: found?.icon }
  }, [multiple, values, value, allOption, placeholder, allOptions])

  return (
    <Popover open={open} onOpenChange={setOpenState}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-label={ariaLabel}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          aria-required={required}
          disabled={disabled}
          className={cn(
            "h-8 w-full min-w-0 justify-between gap-1.5 border-input bg-transparent px-2.5 py-2 text-sm font-normal",
            triggerClassName,
          )}
        >
          <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left">
            {triggerContent.icon ? <span className="flex size-4 shrink-0 items-center justify-center">{triggerContent.icon}</span> : null}
            <span className="truncate">{triggerContent.label}</span>
          </span>
          <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className={cn("w-(--radix-popover-trigger-width) min-w-56 p-0", contentClassName)}
      >
        <Command shouldFilter={false} label={ariaLabel}>
          <CommandInput value={query} onValueChange={setQuery} placeholder={searchPlaceholder} />
          <CommandList id={listId} className="max-h-[min(18rem,50dvh)]">
            {loading ? (
              <p className="px-3 py-4 text-center text-sm text-muted-foreground">{loadingText}</p>
            ) : (
              <>
                {/* Plain text, not cmdk's own `Command.Empty`: with `shouldFilter={false}`
                    and a pinned "All …" row always mounted, cmdk's internal item count
                    never reaches zero, so its own empty-state gate would never fire. */}
                {nothingVisible ? <p className="py-6 text-center text-sm text-muted-foreground">{emptyText}</p> : null}
                {allOption ? (
                  <>
                    <CommandGroup>
                      <CommandItem
                        value={`__all__:${allOption.value}`}
                        onSelect={() => choose(allOption.value)}
                        data-checked={isChosen(allOption.value) ? "true" : undefined}
                      >
                        <OptionRow option={allOption} />
                      </CommandItem>
                    </CommandGroup>
                    {visibleGroups.some((g) => g.options.length > 0) ? <CommandSeparator /> : null}
                  </>
                ) : null}
                {visibleGroups.map((group, index) =>
                  group.options.length === 0 ? null : (
                    <CommandGroup key={group.heading ?? index} heading={group.heading}>
                      {group.options.map((option) => (
                        <CommandItem
                          key={option.value}
                          value={`${index}:${option.value}`}
                          disabled={option.disabled}
                          onSelect={() => choose(option.value)}
                          data-checked={isChosen(option.value) ? "true" : undefined}
                        >
                          <OptionRow option={option} />
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ),
                )}
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
