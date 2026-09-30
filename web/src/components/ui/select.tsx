"use client"

import * as React from "react"
import { Select as SelectPrimitive } from "radix-ui"
import { CheckIcon, ChevronDownIcon, ChevronsUpDownIcon, ChevronUpIcon } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * The one custom select (docs/ui/DESIGN-SYSTEM.md section 6.3), on Radix
 * Select. It replaces every native `<select>`.
 *
 * - Trigger: at least 36 px (30 px `sm`), `--input` border, ellipsised value,
 *   a 15 px tertiary `ChevronsUpDown`; brand border plus `--focus-shadow`
 *   while open.
 * - Popup: at least as wide as the trigger, at most `min(92vw, 440px)` wide
 *   and `min(360px, available height)` tall; 12 px radius, overlay shadow,
 *   5 px padding.
 * - Items: 32 px, `--muted` when highlighted; the selected item is weight 500
 *   with a trailing accent check.
 * - Keyboard: arrows, Enter, Escape, Home/End and type-ahead come from Radix.
 *
 * `SimpleSelect` is a drop-in for a native `<select>` driven by an `options`
 * array.
 */
function Select({ ...props }: React.ComponentProps<typeof SelectPrimitive.Root>) {
  return <SelectPrimitive.Root data-slot="select" {...props} />
}

function SelectGroup({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.Group>) {
  return <SelectPrimitive.Group data-slot="select-group" className={cn("scroll-my-1", className)} {...props} />
}

function SelectValue({ ...props }: React.ComponentProps<typeof SelectPrimitive.Value>) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

export const selectTriggerClasses =
  "flex w-fit min-w-0 items-center justify-between gap-2 rounded border border-input bg-card py-[7px] pr-2.5 pl-[11px] text-left text-control whitespace-nowrap text-foreground transition-[color,border-color,box-shadow] duration-(--duration-fast) outline-none select-none hover:border-border-strong focus-visible:border-brand focus-visible:shadow-focus focus-visible:outline-none data-[state=open]:border-brand data-[state=open]:shadow-focus aria-expanded:border-brand aria-expanded:shadow-focus disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-50 aria-invalid:border-destructive-solid data-placeholder:text-text-tertiary [&_svg]:pointer-events-none [&_svg]:shrink-0"

function SelectTrigger({
  className,
  size = "default",
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger> & {
  size?: "sm" | "default"
}) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={cn(
        selectTriggerClasses,
        "data-[size=default]:h-9 data-[size=sm]:h-[30px] data-[size=sm]:py-1 data-[size=sm]:text-label *:data-[slot=select-value]:truncate *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-1.5 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronsUpDownIcon className="pointer-events-none size-[15px] text-text-tertiary" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectContent({
  className,
  children,
  position = "popper",
  align = "start",
  sideOffset = 4,
  collisionPadding = 16,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  const popper = position === "popper"
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        data-align-trigger={position === "item-aligned"}
        className={cn(
          "relative z-50 max-h-[min(360px,var(--radix-select-content-available-height))] min-w-36 origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-lg border border-border bg-popover text-foreground shadow-overlay duration-(--duration-base) data-[align-trigger=true]:animate-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-97 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-97",
          popper && "max-w-[min(92vw,440px)] min-w-(--radix-select-trigger-width)",
          className
        )}
        position={position}
        align={popper ? align : undefined}
        sideOffset={popper ? sideOffset : undefined}
        collisionPadding={popper ? collisionPadding : undefined}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport data-position={position} className="p-[5px]">
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

function SelectLabel({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn("px-2 pt-1.5 pb-1 text-caption text-text-tertiary", className)}
      {...props}
    />
  )
}

function SelectItem({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex h-8 w-full cursor-default items-center gap-2 rounded-sm pr-8 pl-2 text-control outline-hidden select-none focus:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50 data-[state=checked]:font-medium [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 *:[span]:last:flex *:[span]:last:min-w-0 *:[span]:last:items-center *:[span]:last:gap-2 *:[span]:last:truncate",
        className
      )}
      {...props}
    >
      <span className="pointer-events-none absolute right-2 flex size-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <CheckIcon className="pointer-events-none text-brand" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

function SelectSeparator({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.Separator>) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn("pointer-events-none -mx-[5px] my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

function SelectScrollUpButton({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.ScrollUpButton>) {
  return (
    <SelectPrimitive.ScrollUpButton
      data-slot="select-scroll-up-button"
      className={cn("z-10 flex cursor-default items-center justify-center bg-popover py-1 text-text-tertiary", className)}
      {...props}
    >
      <ChevronUpIcon />
    </SelectPrimitive.ScrollUpButton>
  )
}

function SelectScrollDownButton({ className, ...props }: React.ComponentProps<typeof SelectPrimitive.ScrollDownButton>) {
  return (
    <SelectPrimitive.ScrollDownButton
      data-slot="select-scroll-down-button"
      className={cn("z-10 flex cursor-default items-center justify-center bg-popover py-1 text-text-tertiary", className)}
      {...props}
    >
      <ChevronDownIcon />
    </SelectPrimitive.ScrollDownButton>
  )
}

export interface SimpleSelectOption {
  value: string
  label: React.ReactNode
  disabled?: boolean
  /** Options sharing a group render under that label, in first-seen order. */
  group?: string
}

export interface SimpleSelectProps {
  id?: string
  name?: string
  value: string
  onValueChange: (value: string) => void
  options: SimpleSelectOption[]
  placeholder?: string
  size?: "sm" | "default"
  disabled?: boolean
  required?: boolean
  className?: string
  contentClassName?: string
  "aria-label"?: string
  "aria-labelledby"?: string
  "aria-describedby"?: string
  "aria-invalid"?: boolean
}

/** Radix forbids an empty item value; a native `<option value="">` maps to this. */
const EMPTY_VALUE = "__lkap_empty__"
const toRadix = (value: string) => (value === "" ? EMPTY_VALUE : value)
const fromRadix = (value: string) => (value === EMPTY_VALUE ? "" : value)

/**
 * Drop-in replacement for a native `<select>`: the same `value` string
 * (including `""` for a "None" option), an `options` array, the trigger takes
 * the `id` so a `<label htmlFor>` or `Field` still names it.
 */
function SimpleSelect({
  id,
  name,
  value,
  onValueChange,
  options,
  placeholder,
  size = "default",
  disabled,
  required,
  className,
  contentClassName,
  "aria-label": ariaLabel,
  "aria-labelledby": labelledBy,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
}: SimpleSelectProps) {
  const groups = React.useMemo(() => {
    const ordered: Array<{ label?: string; options: SimpleSelectOption[] }> = []
    for (const option of options) {
      const existing = ordered.find((group) => group.label === option.group)
      if (existing) existing.options.push(option)
      else ordered.push({ label: option.group, options: [option] })
    }
    return ordered
  }, [options])
  const hasValue = options.some((option) => option.value === value)
  return (
    <Select
      name={name}
      value={hasValue ? toRadix(value) : undefined}
      onValueChange={(next) => onValueChange(fromRadix(next))}
      disabled={disabled}
      required={required}
    >
      <SelectTrigger
        id={id}
        size={size}
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        aria-invalid={invalid}
        className={cn("w-full", className)}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={contentClassName}>
        {groups.map((group, index) =>
          group.label ? (
            <SelectGroup key={group.label}>
              {index > 0 ? <SelectSeparator /> : null}
              <SelectLabel>{group.label}</SelectLabel>
              {group.options.map((option) => (
                <SelectItem key={option.value} value={toRadix(option.value)} disabled={option.disabled}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : (
            <React.Fragment key={`ungrouped-${index}`}>
              {group.options.map((option) => (
                <SelectItem key={option.value} value={toRadix(option.value)} disabled={option.disabled}>
                  {option.label}
                </SelectItem>
              ))}
            </React.Fragment>
          )
        )}
      </SelectContent>
    </Select>
  )
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
  SimpleSelect,
}
