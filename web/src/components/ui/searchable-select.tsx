"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import { ChevronsUpDownIcon, PlusIcon } from "lucide-react"

import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { selectTriggerClasses } from "@/components/ui/select"

export interface SearchableSelectOption {
  value: string
  label: string
  icon?: React.ReactNode
  /** Shown right-aligned on the row, e.g. an app count for a category. */
  count?: number
  disabled?: boolean
  /** Stays visible no matter what is typed (an escape hatch like "Other code…" — the one moment it exists for is when nothing else matches). */
  pinned?: boolean
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
  /**
   * Render at most this many rows (default 100) and say how many more there
   * are; typing narrows the list (spec 6.3 "a cap on the number of rows").
   */
  maxRows?: number
  /** Offer the typed text as a value when nothing matches it exactly. */
  allowCustom?: boolean
  /** The free-text row's label. Default: Use “{query}”. */
  customOptionLabel?: (query: string) => string
  triggerClassName?: string
  contentClassName?: string
  "aria-label"?: string
  "aria-describedby"?: string
  "aria-invalid"?: boolean
  "aria-required"?: boolean
}

/** Lower-case, trimmed and accent-insensitive (spec 9: "é" matches "e"). */
function normalize(text: string): string {
  return text.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
}

/** Keep at most `limit` rows across groups (pinned rows always stay). */
function capGroups(groups: SearchableSelectGroup[], limit: number): { groups: SearchableSelectGroup[]; hidden: number } {
  let remaining = limit
  let hidden = 0
  const capped = groups.map((group) => {
    const options: SearchableSelectOption[] = []
    for (const option of group.options) {
      if (option.pinned || remaining > 0) {
        options.push(option)
        if (!option.pinned) remaining -= 1
      } else {
        hidden += 1
      }
    }
    return { ...group, options }
  })
  return { groups: capped, hidden }
}

const PAGE_STEP = 10

function matches(option: SearchableSelectOption, needle: string): boolean {
  if (needle === "" || option.pinned) return true
  const haystack = `${normalize(option.label)} ${normalize(option.value)}`
  // Every word must match somewhere (spec 9).
  return needle.split(/\s+/).every((word) => haystack.includes(word))
}

function filterGroups(groups: SearchableSelectGroup[], needle: string): SearchableSelectGroup[] {
  if (needle === "") return groups
  return groups
    .map((group) => ({ ...group, options: group.options.filter((option) => matches(option, needle)) }))
    .filter((group) => group.options.length > 0)
}

function CountBadge({ count }: { count: number }) {
  return <span className="shrink-0 text-caption text-text-tertiary tabular-nums">{count}</span>
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
 * The searchable combobox (docs/ui/DESIGN-SYSTEM.md section 6.3) for a list
 * too long to scan at a glance (a category filter, a model or id picker):
 * `Popover` + `Command` (cmdk), so typing narrows the list instead of
 * scrolling it. Grouped results ("Current", "Recommended", "All"), a row cap,
 * an optional free-text option, an optional pinned "All …" row and per-option
 * icon/count all come from the one component. Keyboard: arrows, Enter,
 * Escape, Home/End (cmdk) and PageUp/PageDown (10 rows). Matching is
 * accent-insensitive and every typed word must match. Also exported as
 * `Combobox`.
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
  maxRows = 100,
  allowCustom = false,
  customOptionLabel = (text) => `Use “${text}”`,
  triggerClassName,
  contentClassName,
  "aria-label": ariaLabel,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
  "aria-required": required,
}: SearchableSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const [highlighted, setHighlighted] = React.useState("")

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
  const { groups: visibleGroups, hidden: hiddenCount } = capGroups(filterGroups(groups, needle), maxRows)
  const exactMatch = allOptions.some((o) => normalize(o.label) === needle || normalize(o.value) === needle)
  const customText = query.trim()
  const showCustom = allowCustom && !multiple && customText !== "" && !exactMatch
  const CUSTOM_ROW = "__custom__"

  // Row values in render order, for PageUp / PageDown.
  const rowValues: string[] = []
  if (allOption) rowValues.push(`__all__:${allOption.value}`)
  visibleGroups.forEach((group, index) => {
    for (const option of group.options) if (!option.disabled) rowValues.push(`${index}:${option.value}`)
  })
  if (showCustom) rowValues.push(CUSTOM_ROW)

  function onListKeyDown(event: React.KeyboardEvent) {
    if (event.key !== "PageDown" && event.key !== "PageUp") return
    event.preventDefault()
    if (rowValues.length === 0) return
    const current = Math.max(0, rowValues.indexOf(highlighted))
    const step = event.key === "PageDown" ? PAGE_STEP : -PAGE_STEP
    const next = Math.min(rowValues.length - 1, Math.max(0, current + step))
    setHighlighted(rowValues[next])
  }
  // The "All …" row and any `pinned` option (e.g. a "Custom…" escape) stay
  // visible no matter what is typed — they are not themselves a value to
  // search for. Only the *searchable* rows count toward "nothing matches",
  // so the escape hatch reads as an offer alongside the empty state, not as
  // proof something matched.
  const nothingVisible = visibleGroups.every((g) => g.options.every((o) => o.pinned)) && !showCustom

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
        <button
          id={id}
          type="button"
          data-slot="combobox-trigger"
          // eslint-disable-next-line jsx-a11y/role-has-required-aria-props -- Radix PopoverTrigger sets aria-controls to the real popover id
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          aria-required={required}
          disabled={disabled}
          className={cn(selectTriggerClasses, "h-9 w-full", triggerClassName)}
        >
          <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left">
            {triggerContent.icon ? <span className="flex size-4 shrink-0 items-center justify-center">{triggerContent.icon}</span> : null}
            <span className="truncate">{triggerContent.label}</span>
          </span>
          <ChevronsUpDownIcon className="size-[15px] shrink-0 text-text-tertiary" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className={cn("w-(--radix-popover-trigger-width) max-w-[min(92vw,440px)] min-w-56 p-0", contentClassName)}
      >
        <Command
          shouldFilter={false}
          label={ariaLabel}
          value={highlighted}
          onValueChange={setHighlighted}
          onKeyDown={onListKeyDown}
        >
          <CommandInput value={query} onValueChange={setQuery} placeholder={searchPlaceholder} />
          {/* No explicit `id` here: cmdk overwrites whatever `id` prop `CommandList`
              is given with its own internally generated one, so a hand-rolled id
              (and an `aria-controls` on the trigger pointing at it) would name a
              DOM node cmdk never actually creates. Radix's `PopoverTrigger`
              already sets the trigger's `aria-controls` to the popover content's
              real id on its own. */}
          <CommandList className="max-h-[min(360px,50dvh)]">
            {loading ? (
              <p role="status" className="px-3 py-4 text-center text-label text-text-secondary">{loadingText}</p>
            ) : (
              <>
                {/* Plain text, not cmdk's own `Command.Empty`: with `shouldFilter={false}`
                    and a pinned "All …" row always mounted, cmdk's internal item count
                    never reaches zero, so its own empty-state gate would never fire. */}
                {nothingVisible ? <p className="py-6 text-center text-label text-text-secondary">{emptyText}</p> : null}
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
                {hiddenCount > 0 ? (
                  <p data-slot="combobox-more" className="px-2.5 py-2 text-caption text-text-tertiary">
                    {hiddenCount} more. Type to narrow the list.
                  </p>
                ) : null}
                {showCustom ? (
                  <>
                    <CommandSeparator />
                    <CommandGroup>
                      <CommandItem value={CUSTOM_ROW} onSelect={() => choose(customText)} data-slot="combobox-custom">
                        <PlusIcon aria-hidden="true" className="text-text-tertiary" />
                        <span className="min-w-0 flex-1 truncate">{customOptionLabel(customText)}</span>
                      </CommandItem>
                    </CommandGroup>
                  </>
                ) : null}
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** Alias: spec 6.3 calls it the combobox. */
export { SearchableSelect as Combobox }
