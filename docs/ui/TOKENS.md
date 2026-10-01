# LKAP web console tokens

The design-system tokens (`docs/ui/DESIGN-SYSTEM.md` section 2) live in one file:
`web/src/app/globals.css`. Components read them only through `var(--token)` or the Tailwind utilities
the `@theme inline` bridge generates.

- `:root` holds the light literals and `.dark` holds the dark literals.
- A shared `:root, .dark` block holds the theme-invariant values (radius, duration, easing, layout,
  font stacks) and every `var()` alias. Declaring an alias in both scopes makes it re-resolve inside any
  `.dark` subtree, such as the `/s/[slug]` wrapper, instead of inheriting the light value from `<html>`.
- `web/tests/design-tokens.test.ts` fails if a spec token is missing from `:root`, `.dark` or the
  bridge.

## Utilities the bridge generates

| Token group | Utility |
|---|---|
| Colours (`--card`, `--text-secondary`, `--success-subtle`, `--chart-3`, …) | `bg-card`, `text-text-secondary`, `bg-success-subtle`, `fill-chart-3` (always `--color-<token>`) |
| `--elevation-raised` / `-overlay` / `-modal`, `--focus-shadow` | `shadow-raised`, `shadow-overlay`, `shadow-modal`, `shadow-focus` |
| `--radius-sm`, `--radius`, `--radius-lg`, `--radius-dialog`, `--radius-pill` | `rounded-sm` (6), `rounded` (8), `rounded-lg` (12), `rounded-dialog` (14), `rounded-pill` |
| `--duration-fast` / `-base` / `-slow` | `duration-(--duration-fast)` (Tailwind has no duration namespace) |
| `--ease-entrance` | `ease-entrance` |
| `--layout-topbar`, `--layout-panel-inset`, `--layout-bottombar` | `h-topbar`, `p-panel-inset`, `h-bottombar` (spacing namespace) |
| `--type-*` (spec section 3) | `text-tab` 11, `text-nav` 11.5, `text-caption` 12, `text-stat-label` 12.5, `text-label` 13, `text-control` 13.5, `text-body` 14, `text-title` 15, `text-dialog` 17, `text-page` 22, `text-stat` 24, `text-display` 26 |

Tabular figures use Tailwind's built-in `tabular-nums`. Merge classes with `cn` from `@/lib/utils`. It
is configured with this type scale, `shadow-raised|overlay|modal|focus` and `rounded-dialog|pill`, so a
`text-label` is not mistaken for a colour.

## Component CSS in `globals.css` (UI-2)

- `[data-slot=status-dot][data-pulse]`: the live dot, opacity 1 to .35 over 1.6 s.
- `[data-slot=skeleton]`: `--muted` with a `--muted-strong` sweep every 1.4 s.
- `[data-slot=spinner]`: 14 px, 2 px ring, accent top edge, 0.8 s.
- All three stop under `prefers-reduced-motion`, with the state meter.
- `input[type=checkbox|radio] { accent-color: var(--brand) }`.
- Toast offsets: `--toast-offset` 24 px, `--toast-offset-mobile` 16 px and `--toast-offset-mobile-bottom`,
  which lifts above the phone tab bar when the shell renders `[data-slot="bottom-tab-bar"]`.

## Additions to the spec

- **`--{success,warning,info,destructive}-foreground`.** This is the text on each status solid. It is
  white in light (warning is near-black), and near-black in dark, following the spec's own
  `--brand-foreground` recipe.
- **Chart values.** The spec gives only the order (slate, blue, amber, teal, violet, green, orange,
  grey). Each series is at least 3:1 on card and on background in both themes.
- **Dark elevation.** These are the spec's shadows in black at 30 to 75% opacity.

## Accent: indigo (UI-R1)

The accent moved from teal (hue 188 to 190) to indigo (hue 280), following the spec's own recipe (section 2.2):
a 48% fill under white text, hover and active 5 points darker on the same hue, a 96% subtle and an 86% border,
and a 75% accent with a near-black foreground in dark mode. Chroma steps down slightly as lightness drops, as the
teal scale did. The light subtle stays at `.018` chroma, the most that hue 280 holds in sRGB at 96% L.

| Token | Light | Dark |
|---|---|---|
| `--brand` | `oklch(48% .16 280)` (`#504cb4`) | `oklch(75% .12 280)` (`#9fa5f9`) |
| `--brand-hover` | `oklch(43% .15 280)` | `oklch(70% .12 280)` |
| `--brand-active` | `oklch(38% .135 280)` | `oklch(65% .12 280)` |
| `--brand-foreground` | `oklch(100% 0 0)` | `oklch(19% .035 280)` |
| `--brand-subtle` | `oklch(96% .018 280)` | `oklch(27% .05 280)` |
| `--brand-border` | `oklch(86% .06 280)` | `oklch(42% .09 280)` |
| `--ring` | `oklch(56% .15 280)` | `oklch(72% .12 280)` |

Ratios from `pnpm check:contrast` (212/212 pairs pass):

| Pair | Light | Dark | Target |
|---|---|---|---|
| `--brand-foreground` on `--brand` / `-hover` / `-active` | 6.91 / 8.54 / 10.48 | 8.14 / 6.77 / 5.58 | 4.5 |
| `--brand` (link, active icon) on card / background / popover | 6.91 / 6.63 / 6.91 | 7.86 / 8.33 / 7.41 | 4.5 |
| `--brand` on muted / sidebar / sidebar-hover / sidebar-active | 6.26 / 6.30 / 5.73 / 6.91 | 7.12 / 8.69 / 7.78 / 7.12 | 4.5 |
| `--brand` on `--brand-subtle` | 6.14 | 6.68 | 4.5 |
| `--foreground` on `--brand-subtle` (list-search highlight) | 15.92 | 13.54 | 4.5 |
| `--ring` on card / background / popover / muted / sidebar | 4.86 / 4.67 / 4.86 / 4.41 / 4.43 | 7.05 / 7.46 / 6.64 / 6.38 / 7.79 | 3 |

**Apart from the status colours.** Info stays at hue 250 and success stays green. Neither scale changed.
`tests/design-tokens.test.ts` holds the accent to one hue and to a CIEDE2000 distance of at least 10 from
`--info-solid`, `--info-text`, `--success-solid` and `--success-text` in both themes (the closest pair today is
the light accent against the info text, at 11.2. The rest read 12.1 to 17.2 against info and 45 or more against success).

**Charts.** `--chart-4` stays teal. It was never read as the accent, and the categorical set still needs a hue
between blue (`--chart-2`, 255) and green (`--chart-6`, 150). An indigo series would sit between `--chart-2` and
`--chart-5` (violet, 295) and blur both.

**Browser chrome.** `src/lib/browser-colors.ts` mirrors the new `--brand` as `#504cb4` (light) and `#9fa5f9`
(dark). `--background` is unchanged.

## Deviations from the spec's literal values

Every fix moves lightness only. Chroma, hue and targets are unchanged. `pnpm check:contrast` checks 212
pairs across both themes (V added the list-search highlight, `--foreground` on `--brand-subtle`).

| Theme | Token | Spec | Shipped | Pair it fixes | Before | After |
|---|---|---|---|---|---|---|
| Light | `--input` | `66% .012 255` | `64% .012 255` | input edge on `--background` / `--muted` (3:1) | 2.99 / 2.82 | 3.23 / 3.05 |
| Light | `--text-tertiary` | `53.5% .014 260` | `52% .014 260` | tertiary text on `--sidebar-hover` / `--muted-strong` (4.5:1) | 4.28 / 4.31 | 4.57 / 4.59 |
| Light | `--warning-solid` | `66% .14 70` | `64.5% .14 70` | warning dot on `--warning-subtle` (3:1) | 2.92 | 3.09 |
| Dark | `--input` | `52% .012 260` | `53% .012 260` | input edge on `--muted` (3:1) | 2.95 | 3.07 |
| Dark | `--success-foreground` | white | `19% .03 150` | text on the success solid (4.5:1) | 2.17 | 8.44 |
| Dark | `--info-foreground` | white | `19% .03 250` | text on the info solid (4.5:1) | 2.47 | 7.48 |
| Dark | `--destructive-foreground` | white | `17% .03 27` | text on the destructive solid (4.5:1) | 3.66 | 6.65 |
| Dark | `--destructive-solid` | `64% .18 27` | `70% .18 27` | legacy `text-destructive` on its resting `bg-destructive/20` tint over card (4.5:1) | 3.87 | 4.63 |
| Dark | `--destructive-hover` | `59% .18 27` | `65% .18 27` | foreground on the hover fill (4.5:1), keeps the spec's 5-point step below the solid | 4.49 (white) | 5.47 |

The two dark destructive rows were meant to be **transitional**. The spec's values failed a legacy style
(the vendored destructive button, badge and menu item put `text-destructive` on a `bg-destructive/20`
tint), and the plan was to return both tokens to 64% and 59% once UI-2 moved those to
`--destructive-text` on card.

**Verification (V) re-checked and kept 70% / 65%.** `components/ui/**` no longer uses the legacy pair,
but two things still fail at the spec values in dark (measured with the same culori pipeline):

| Pair at the spec values (dark) | Ratio | Target | Where it is live |
|---|---|---|---|
| `text-destructive` on `bg-destructive/10` over card | 4.42 | 4.5 | leave-alone `components/agents-ui/agent-track-toggle.tsx` and `agent-control-bar.tsx`: the mic and camera "off" state on the always-dark caller page (`CONTROL_BAR_BRAND_ON` only restyles the on state and End call) |
| `--destructive-foreground` (near-black) on `--destructive-hover` 59% | 4.28 | 4.5 | every danger button's hover fill |

At the shipped 70% / 65% the same pairs read 5.46 and 5.47. Returning to the spec needs a product call
(docs/ui/REPORT.md): either restyle the agents-ui off state from outside, as `session-controls.tsx`
already does for End call, and move the hover step (for example 61%), or accept the shipped values as the
dark palette.

**Decision O1 (UI-R1): accepted.** 70% and 65% are the dark palette for the destructive solid and hover. The spec
values are not coming back.

**Icon rule.** `globals.css` carries the spec's literal rule, `.lucide { width:16px; height:16px;
stroke-width:1.75px; flex:none }`. The `Icon` wrapper sizes through `size-*` utilities, which beat the
base-layer rule. No icon sets its own stroke width.

## Legacy names

The old names are `var()` aliases of the spec tokens in the shared block, so existing screens keep
rendering. The full mapping is in `docs/ui/AUDIT.md` section 4. The notable changes:

- `--primary` is now the accent.
- `--success` no longer aliases `--brand`.
- `--danger*` and `--destructive` both point at the destructive scale.
- `--muted-foreground` is `--text-secondary`.
- `--radius-xs` is 6 px and `--radius-xl` is 14 px.
- `--dur-*` are the spec durations.

**Decision O2 (UI-R1).** Every file outside `components/ui/`, `components/agents-ui/` and `panels/` now uses the
spec names. The last 47 uses in 22 files moved over with identical values (for example `text-muted-foreground` to
`text-text-secondary`, `bg-danger-soft` to `bg-destructive-subtle`, `text-brand-text` to `text-brand`, `ease-out`
to `ease-entrance`, `rounded-xl` to `rounded-dialog`, `shadow-md` to `shadow-overlay`, `var(--danger)` to
`var(--destructive-solid)`). The design lint's `legacy-token` rule now forbids the alias utilities and `var()`
reads everywhere else. The vendored `components/ui/**` primitives are exempt by an allowlist line, and
`panels/**` and `agents-ui/**` stay exempt as leave-alone code, so the aliases stay defined for them. The
session-only survivors (`--stage`, `--radius-2xl`, `--ease-in-out`, `--ease-drawer`) are not aliases and are not
part of the rule.

## Guard rails

- **`pnpm lint:design`** (`web/scripts/design-lint.mjs`, also in `pnpm lint` and the vitest suite)
  forbids colour literals and raw shadows outside `globals.css`. It also forbids native selects,
  browser dialogs, per-icon stroke widths, sparkle icons, palette utilities, opacity-modified colours,
  legacy token aliases outside the vendored trees, and CSS comment hazards.
- **Allowlist.** Today's violations are listed per file in `web/scripts/design-lint-allowlist.txt`.
  The list only shrinks.
- **Browser colours.** `web/src/lib/browser-colors.ts` holds the only other colour literals: the
  `theme-color` and manifest mirrors of `--background` and `--brand`.
  `web/tests/browser-colors.test.ts` pins them to the tokens.
