# Shared primitives

Contract: `docs/ui/DESIGN-SYSTEM.md` section 6 (primitives), 7.3 (page template), 8 (states). UI-2 (`docs/ui/AUDIT.md`)
built them; screens compose them and never restyle them. Everything is on show at `/console/preview/styleguide`.

Nothing in `components/shared/**` imports screen code. The permission helpers read `components/console/lib/roles`
(`new-resource-button`, `require-write`), as before. In session code import per file
(`@/components/shared/state-meter`); console code may use the barrel `@/components/shared`.

**Import `cn` from `@/lib/utils`**, not from the `cn` package: the configured one knows the type scale, so
`text-label` is a font size and survives next to `text-info-text`.

## Decisions

- **Button default variant.** A variant-less `<Button>` renders as `secondary` (card, hairline, raised shadow),
  not the accent. About 240 console buttons have no variant; mapping them to the accent would put several accent
  buttons in one view (AUDIT P3). Each view marks its **one** main action `variant="primary"`, placed last. The
  legacy names stay as aliases: `default` and `outline` render as `secondary`, `destructive` as `danger-outline`,
  `brand` as `primary`; `data-variant` keeps the caller's value. The primitives that are a page's main action
  already opt in (`NewResourceButton`, `ConfirmDialog`'s confirm).
- **Permissions (D12).** Hide row and edit actions a person can't use (`IfCan`, `RowMenu` hides itself when
  empty); replace page-level primaries with a `ReadOnlyNote` that names the next step. Single controls use
  `useWriteGate` (hidden when unusable, disabled while the role loads). The old `GatedButton` (disabled plus
  tooltip) is gone (O3, UI-R1).
- **Dialogs only.** No sheet or drawer primitive exists (D7); large forms use `DialogContent size="lg|xl"`.
- **Errors.** Everything a person reads goes through `friendlyError` (`src/lib/friendly-error.ts`, pure and free of console code, so the caller page can borrow its sentences through `codeMessage` / `statusMessage`);
  `errorMessage()` returns its text. The raw detail is on `.raw` for logs only.
- **Sign-in errors (S8).** `/login` wraps it in `app/login/sign-in-errors.ts`: the shared 401 copy ("Your session has ended") is wrong where you sign in, so auth, rate-limit, server and invite failures get their own plain copy there (never saying whether the email or the password was wrong); everything else falls through to `friendlyError`.

## Inventory

| Area | File | Exports |
|---|---|---|
| Buttons (6.1) | `ui/button.tsx`, `ui/spinner.tsx` | `Button` (`primary`, `secondary`, `ghost`, `danger`, `danger-outline`, `link`, `link-destructive`, `link-neutral`; sizes `sm`, `default`, `lg`, `icon`, `icon-md`, `icon-sm`, `block`; `busy` + `busyLabel`), `IconButton`, `Spinner` |
| Busy copy | `shared/busy-label.ts` | `gerund`, `busyLabelFor("Delete agent")` → "Deleting agent…" |
| Form controls (6.2) | `ui/input.tsx`, `ui/textarea.tsx`, `ui/label.tsx`, `ui/checkbox.tsx`, `ui/radio-group.tsx`, `ui/switch.tsx`, `ui/input-group.tsx` | restyled to 36 px, `--input` edge, brand border + `--focus-shadow` |
| Field | `shared/field.tsx` | `Field` (`optional` → "(optional)"; hint; error), `FieldRow`, `FormError`, `fieldIds` |
| Choices | `shared/choice.tsx` | `CheckboxRow`, `RadioRow`, `OptionCard` (`:has(input:checked)`), `SwitchRow` |
| Search and icons | `shared/search-field.tsx` | `InputWithIcon`, `SearchField` (Escape clears; X only with a value) |
| List search (spec 9) | `shared/list-search.tsx` | The one list search for every list: `useListSearch`, `ListSearchField`, `ListNoMatches`, `ListToolbar` (search + segmented filter), `matchesQuery`, `matchRanges`, `Highlight` (foreground on `--brand-subtle`), `useRememberedQuery`, `useRememberedChoice`, `readStoredFilters` / `writeStoredFilters`. Remembered under `lkap:list:<id>`; old `lkap:settings:search:<id>` values (and a caller's `legacyKeys`) migrate once on read |
| Secrets | `shared/password-input.tsx` | `PasswordInput` (show/hide), `SecretInput` (write-only) |
| Chips | `shared/chip-input.tsx` | `ChipInput` (Enter/comma/semicolon, paste splits, invalid kept and flagged, polite live region), `validateEmail` |
| Files | `shared/file-input.tsx` | `FileInput` |
| Select (6.3) | `ui/select.tsx` | `Select*` parts, `SimpleSelect` (drop-in for native `<select>`: `options`, `""` values, groups) |
| Combobox (6.3) | `ui/searchable-select.tsx` | `SearchableSelect` / `Combobox` (groups, `maxRows`, `allowCustom`, PageUp/PageDown, accent-insensitive) |
| Dialog (6.4) | `ui/dialog.tsx` | `Dialog*` (560 default, `sm` 440, `lg` 760, `xl` 980; sticky muted footer) |
| Confirm | `console/shared/confirm-dialog.tsx` | `ConfirmDialog` (`alertdialog` when destructive, danger button, gerund busy label, inline error), `TypedConfirmDialog` |
| Menus, popovers, tooltips | `ui/dropdown-menu.tsx`, `ui/popover.tsx`, `ui/tooltip.tsx`, `shared/row-menu.tsx` | `RowMenu` (destructive item last, after a separator) |
| Toasts | `ui/sonner.tsx` | `Toaster` (bottom-right, lifted above the phone tab bar; success/info 6 s; errors/warnings persist; `role="alert"` for errors), `applyToastPolicy`, `toast` |
| Alerts (6.5) | `ui/alert.tsx`, `console/shared/error-banner.tsx` | `Alert tone=… title actions` (role `alert` for danger), `ErrorBanner error={…} onRetry`, `errorMessage` |
| Empty states | `shared/empty-state.tsx` | `EmptyState` (dashed card), `NoMatches` (SearchX, Clear filters) |
| Loading | `ui/skeleton.tsx`, `shared/loading-state.tsx` | `Skeleton`, `SkeletonText` (ragged last line), `LoadingRegion`, `SkeletonRows`, `LoadingRow` |
| Progress | `ui/progress.tsx`, `shared/progress-steps.tsx` | `Progress`, `StepList` (`aria-current="step"`), `ProgressSteps` |
| Status (6.6) | `shared/status-chip.tsx`, `shared/status-map.ts`, `ui/badge.tsx`, `shared/tag.tsx` | `StatusPill` (dot always; `live` pulses), `LifecycleBadge` (`progress` on a working state: "Importing 40%" / "Indexing…"), `lifecycleStatus`, `LIFECYCLE` (waiting = warning, working on it = info, `enabled` / `disabled`), `WORKING_STATES`, `isWorkingState`, `StatusChip` (deprecated alias), `Badge`, `Tag`, `TagList` |
| Data display (6.7) | `ui/card.tsx`, `ui/table.tsx`, `shared/list-card.tsx`, `shared/data-display.tsx`, `shared/description-list.tsx`, `shared/responsive-table.tsx`, `shared/section.tsx` | `Card*` + `CardInset`, `Table` (`framed`, `numeric`), `ListCard`, `ListCardRow`, `MetaList`, `StatCard`, `StatGrid`, `Avatar`, `initials` |
| Navigation | `shared/segmented-control.tsx`, `ui/tabs.tsx`, `shared/theme-switcher.tsx`, `ui/kbd.tsx` | `SegmentedControl`, `Tabs*` + `TabsCount` (accent underline), `ThemeSwitcher`, `Kbd` |
| Page (7.3) | `shared/page-header.tsx` | `Page` (`default` 1200, `wide` 1440, `narrow` 880), `PageHeader` (`back`, `eyebrow`, `badge`, `actions` with the primary last) |
| Permissions (8.5) | `console/shared/permission.tsx`, `console/shared/write-gate.ts`, `shared/read-only-note.tsx`, `shared/new-resource-button.tsx`, `shared/require-write.tsx` | `IfCan`, `useCan`, `useWriteGate` (`show` / `pending` / `can`: hidden when unusable, disabled while the role loads), `readOnlyCopy`, `ReadOnlyNote`, `NewResourceButton`, `RequireWrite` |
| Danger zone (7.4) | `console/shared/danger-zone-card.tsx` | `DangerZoneCard`: a detail page's last card, danger-outline entry and a typed `DELETE` confirmation listing what goes |
| Phone tables (10) | `shared/stacked-table.tsx` | `STACKED_TABLE`, `STACKED_CONTROL`, `PhoneLabel`: editable tables stack into blocks below `sm` instead of scrolling sideways |
| Worker errors | `shared/status-error.ts` | `plainStatusError(raw, fallback)`: a stored import/indexing failure reason, shown only when it reads as plain copy |
| Offline (8.8) | `hooks/use-online-status.ts`, `console/shell/offline-banner.tsx`, `session/connection-banner.tsx` | `useOnlineStatus`; the console shell mounts `OfflineBanner` once ("Changes save when you're back online. Reconnecting…", and the API-down alert steps aside); the caller page says it on the pre-call card and, mid-call, in `ConnectionBanner offline`. Screens don't add their own |
| Icons (5) | `shared/icon.tsx` | `Icon` (sizes `xs` 12, `sm` 14, `select` 15, `md` 16, `nav` 17, `tile` 18, `lg` 20, `xl` 24, via `size-*` classes) |
| Third-party marks (5) | `shared/vendor-mark.tsx`, `shared/vendor-marks.ts`, `shared/vendor-mark-data.ts` (generated) | `VendorMark` (`labelled` for a mark with no printed name; decorative otherwise), `vendorMarkFor`, `vendorKey`, `VENDOR_MARKS`: Simple Icons first, Lobe Icons second (both copied per icon by `scripts/gen-vendor-marks.mjs`; the packages are dev-only), the tinted monogram last; never imported by `/s/[slug]` |

Earlier primitives keep their contracts: `StateMeter`, `CopyButton`, `RelativeTime`, `VendorMark`,
`CapabilityBadge`, `agent-state`, `capability-meta`, `panel-meta`.

Tests: `tests/shared-primitives.test.tsx`, `tests/ui-*.test.tsx`, `tests/friendly-error.test.ts`,
`tests/searchable-select.test.tsx`, `tests/styleguide.test.tsx`.
