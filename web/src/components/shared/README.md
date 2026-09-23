# Shared primitives (WP-0)

Contract source: `docs/UI_UX_SPEC.md` §2.7. Everything here is importable from **both** surfaces: nothing in `components/shared/**` imports `components/console/**`. In session code, import per file (`@/components/shared/state-meter`), not from the barrel. Console code may use the barrel `@/components/shared`.

Tests: `web/tests/shared-primitives.test.tsx`.

## Primitives

| Component | Import | Props | Notes |
|---|---|---|---|
| `StateMeter` | `@/components/shared/state-meter` | `state: MeterState` (`"idle"\|"connecting"\|"listening"\|"thinking"\|"speaking"\|"failed"\|"ended"`), `size?: "xs"\|"sm"\|"md"\|"lg"` (default `sm`), `bars?: 4\|5` (default 4; 5 for `lg`; `xs` always 2), `label?: boolean`, **`level?: number`** (0–1, extra), `className?` | CSS-only (keyframes in `globals.css`, `[data-slot=state-meter]`). Exposes `data-state`, `data-size`; `role="img"` + `aria-label` from `STATE_LABEL`. `level` switches the bars to input-driven heights (mic check, speaking) and sets `data-level`. Reduced motion stops the animation, keeps colour. |
| `StatusChip` | `@/components/shared/status-chip` | `tone: "neutral"\|"info"\|"success"\|"warning"\|"danger"\|"live"`, `dot?: boolean`, `size?: "sm"\|"md"` (default `md`), `children`, `className?` | `rounded-xs`, `*-soft` bg + `*-text`. `live` renders a decorative `StateMeter xs` in the **`listening`** state (the tally lamp). Use instead of `Badge` for status. |
| `Icon` | `@/components/shared/icon` | `as: LucideIcon`, `size?: "sm"\|"md"\|"lg"\|"xl"` (14/16/20/24, default `md`), `label?: string`, plus any Lucide svg prop | stroke 1.75, `absoluteStrokeWidth`; `aria-hidden` unless `label` (then `role="img"`). Lucide only. |
| `EmptyState` | `@/components/shared/empty-state` (also `@/components/console/shared/empty-state`) | `icon?: LucideIcon \| ReactElement`, `title: string`, `description?: string`, `action?: ReactNode`, `secondary?: ReactNode`, `compact?: boolean`, `className?` | Full: centered icon tile + h3 + one sentence + action. `compact`: one line + inline action (in-section empties). No dashed box. |
| `PageHeader` | `@/components/shared/page-header` (also `@/components/console/shared/page-header`) | `title: ReactNode`, `description?`, `eyebrow?`, `breadcrumbs?: {label: string; href?: string}[]`, `actions?`, `sticky?: boolean`, `className?` | h1 at 22/28. Last breadcrumb is the current page. `sticky` adds `bg-background` + hairline and bleeds to the gutter (`-mx-4 md:-mx-6`). Has `mb-6` by default. |
| `Section` | `@/components/shared/section` | `id: string`, `title: ReactNode`, `description?`, `aside?`, `children`, `className?` | Card replacement: one bordered card, h2 title row, body is `divide-y`. Region labelled by the title (`${id}-title`). Never nest cards inside. |
| `SectionRow` (extra) | `@/components/shared/section` | `compact?: boolean` + div props | Standard row padding (20 px / 16 px compact). |
| `Field` | `@/components/shared/field` | `label`, `htmlFor: string`, `hint?`, `error?`, `required?`, `optional?`, `children`, `inline?: boolean`, `className?` | Required/Optional are words in the hint slot ("Required · hint"). When `children` is a single element it gets `aria-describedby` (`${htmlFor}-hint`, `${htmlFor}-error`), `aria-invalid` and `aria-required`. For composite controls use `fieldIds(htmlFor)`. `inline` = `grid-cols-[minmax(0,1fr)_auto]` (switch rows; never clipped). |
| `DescriptionList` | `@/components/shared/description-list` | `items: {term: ReactNode; detail: ReactNode; mono?: boolean}[]`, `columns?: 1\|2\|3`, `className?` | Columns apply ≥ 768 px. `mono` = Geist Mono + tabular nums. |
| `CopyButton` | `@/components/shared/copy-button` | `value: string`, `label: string` (accessible name), `size?: "xs"\|"sm"\|"md"`, `className?` | Ghost icon button; check glyph for 1.2 s (`COPY_FEEDBACK_MS`); announces "Copied"/"Couldn't copy" via a polite live region. Client component. |
| `RelativeTime` | `@/components/shared/relative-time` | `iso: string \| number`, `withExact?: boolean`, `className?` | `<time>` with `title` = exact. First render shows the exact time (SSR-safe), then "4 min ago" after mount; refreshes every 30 s. `withExact` → "19 Sep, 03:04 (4 min ago)". Client component. |
| `VendorMark` | `@/components/shared/vendor-mark` | `vendor: string`, `size?: "sm"\|"md"\|"lg"` (20/24/32), `className?` | Two-letter monogram (`vendorMonogram`), tint from a stable per-vendor hue mixed into tokens. `role="img"`, named by `vendor`. No logos. |
| `CapabilityBadge` | `@/components/shared/capability-badge` | `kind: "vision"\|"realtime"\|"tools"\|"silent-tools"\|"voices"\|"no-key"\|"key-required"\|"key-set"`, `count?: number`, **`children?`** (extra: overrides the word), `className?` | Icon + word, micro type. `count` → "12 voices". `no-key` success tone, `key-required` warning tone, others neutral. For `key-set` pass `"<label> · <fingerprint>"` as children. Metadata: `CAPABILITY_BADGE_META`. |
| `Kbd` | `@/components/shared/kbd` | `children`, `className?` | Wraps `@/components/ui/kbd` (mono, `rounded-xs`). `KbdGroup` re-exported. |
| `ResponsiveTable<T>` | `@/components/shared/responsive-table` | `columns: {id; header; cell: (row) => ReactNode; className?; headerClassName?; align?: "start"\|"end"; interactive?: boolean}[]`, `rows: T[]`, `renderCard: (row) => ReactNode`, `rowHref?: (row) => string \| undefined`, extras: `getRowKey?`, `label?` (aria name), `empty?: ReactNode`, `className?` | Table ≥ 768 px, card list below, switched by CSS (both in the DOM; no `matchMedia`). With `rowHref` the first cell becomes a stretched link with a focus ring, and each card gets a full-card link named by the card text. Mark columns holding buttons/menus `interactive: true` so they sit above the row link; card controls must be real `<a>`/`<button>`. Avoid `id`s inside cells (rendered twice). |

### State vocabulary — `@/components/shared/agent-state`

- `AgentUiState` = `"connecting"\|"listening"\|"thinking"\|"speaking"\|"reconnecting"\|"failed"\|"ended"` (the session's state model, §5.4).
- `MeterState` = `"idle"\|"connecting"\|"listening"\|"thinking"\|"speaking"\|"failed"\|"ended"` (what `StateMeter` draws).
- `AGENT_STATE_LABEL: Record<AgentUiState, string>` — Connecting · Listening · Thinking · Speaking · Reconnecting · Failed · Ended (no ellipsis; add "…" in captions where §5.4 shows it).
- `STATE_LABEL: Record<MeterState, string>` — same words + "Idle".
- `toMeterState(state: AgentUiState): MeterState` — `reconnecting` → `connecting` (§5.4); everything else passes through.
- `AGENT_UI_STATES`, `METER_STATES` — ordered arrays for previews/tests.

## Other WP-0 exports

| What | Import | API |
|---|---|---|
| Format helpers | `@/lib/format` | `toMillis(ts: string\|number)` (ISO → `Date.parse`; number < 1e11 → seconds × 1000; numeric strings too; `NaN` if invalid), `formatDateTime(ts, {seconds?: false, timeZone?})` → "19 Sep, 03:04" (year added outside the current year), `formatTime(ts, opts)` → "03:04", `formatDuration(ms)` → "42s" / "1m 42s" / "1h 5m" ("—" if invalid), `formatBytes(n)` → "1.5 KB", `pluralize(n, one, many)` → "3 chunks", `formatRelative(ts, now?, opts?)` → "4 min ago" (extra). All accept ISO or epoch. |
| Theme provider | `@/components/console/shell/theme-provider` | `<ThemeProvider>{children}</ThemeProvider>` — next-themes, `attribute="class"`, `storageKey="lkap-theme"`, `defaultTheme="light"`, `enableSystem`, `disableTransitionOnChange`. **Already mounted in `app/console/layout.tsx`** (keep it when rewriting the layout). |
| Theme hook | `@/lib/theme` | `useThemePreference()` → `{theme, resolvedTheme, setTheme, options, current, mounted}`; `THEME_OPTIONS` (`{value, label, icon}` Light/Dark/System), `ThemePreference`, `THEME_STORAGE_KEY`, `DEFAULT_THEME`, `isThemePreference`. |
| Console hooks | `@/components/console/lib/api-hooks` | `useHealth()` (`GET health`, stale 30 s), `useUpdateCredential()` (`mutate({id, body: CredentialUpdate})`, PUT; omit `secrets` to keep them), `useTestCredential()` (`mutate(id)` → `CredentialTestResult`), `useSessions(agentId?, status?, limit?)` (limit slices `items` client-side, `total` unchanged, shares the cache). |
| Console constants | `@/components/console/lib/constants` | `TONE_BADGE_CLASSES` (token classes), `BLOCK_TOOLS` (the four panel-block tool switches), `CAPABILITY_META: Record<"camera"\|"screen_share"\|"chat_input"\|"vision_inject_per_turn", {label, description, iconName, icon}>`. Console-only — never import from session code. |
| Panel metadata | `@/components/shared/panel-meta` | `PANEL_META: Record<string, {label, description, layout, blocksAware?, legacy?}>` and `panelMeta(id)` for every registered panel (V2-11, F-30; keys held equal to `PANELS` by `tests/registry.test.tsx`). Plain data — importable from both surfaces. |
| Console shared | `@/components/console/shared/*` | `ErrorBanner` (danger-soft `Alert`, "Try again"), `ValidationBanner` (tokens), `ConfirmDialog` (solid destructive confirm), `EmptyState`/`PageHeader` re-exports. Same props as before. |

## UI kit changes (`@/components/ui/*`)

- `Button`: press feedback `active:scale-[0.98]` at `--dur-1`; radius `rounded-sm` (6 px); focus = 2 px brand ring + 2 px offset; new `variant="brand"` (signal green, for "live"/Start call only) and `size="xl"` (h-11, session Start call).
- `Badge`: new `tone` prop (`neutral\|brand\|info\|success\|warning\|danger`) — prefer `StatusChip` for status.
- `Alert`: new variants `info\|success\|warning\|danger` (soft surfaces).
- `TooltipProvider`: defaults `delayDuration=400`, `skipDelayDuration=300`. The shadcn `Sidebar` does **not** include a `TooltipProvider`; wrap the shell in one.
- `Sidebar`: width 232 px (`14.5rem`), rail 56 px (`3.5rem`); mobile breakpoint from `@/hooks/use-mobile` (768 px).
- Added: `sidebar`, `tooltip`, `dropdown-menu`, `popover`, `command` (+ `cmdk`), `separator`, `progress`, `alert`, `label`, `checkbox`, `radio-group`, `scroll-area`, `collapsible`, `breadcrumb`, `kbd`, `input-group`. (`sheet` was added here and later deleted: side drawers are not allowed — docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §5.)

## Tokens (utilities)

`bg-brand text-brand-foreground`, `bg-brand-soft text-brand-text`, `border-brand-line`, `bg-{info,success,warning,danger}` / `-soft` / `-text`, `bg-stage text-stage-foreground`, `rounded-{xs,sm,md,lg,xl,2xl}` (4/6/8/12/16/20 px), `shadow-{sm,md,lg}`, `ease-out` / `ease-in-out` / `ease-(--ease-drawer)` (the tokens), `duration-(--dur-1…4)`, `font-hand` / `font-hand-label` (once WP-8/WP-10 define `--font-hand*`). No `emerald-*/sky-*/amber-*/red-*/blue-*` classes.

Contrast gate: `pnpm check:contrast` (`scripts/check-contrast.mjs`). Run it after touching any token; if a pair fails, change the token's lightness, never the target.
