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

Tabular figures use Tailwind's built-in `tabular-nums`.

## Additions to the spec

- **`--{success,warning,info,destructive}-foreground`.** This is the text on each status solid. It is
  white in light (warning is near-black), and near-black in dark, following the spec's own
  `--brand-foreground` recipe.
- **Chart values.** The spec gives only the order (slate, blue, amber, teal, violet, green, orange,
  grey). Each series is at least 3:1 on card and on background in both themes.
- **Dark elevation.** These are the spec's shadows in black at 30–75% opacity.

## Deviations from the spec's literal values

Every fix moves lightness only; chroma, hue and targets are unchanged. `pnpm check:contrast` checks 210
pairs across both themes.

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
| Dark | `--destructive-hover` | `59% .18 27` | `65% .18 27` | foreground on the hover fill (4.5:1); keeps the spec's 5-point step below the solid | 4.49 (white) | 5.47 |

The two dark destructive rows are **transitional**. The spec's own values fail only a legacy style: the
vendored destructive button, badge and menu item still put `text-destructive` on a `bg-destructive/20`
tint. Once UI-2 moves those to `--destructive-text` on card, and the verification package drops that
guard pair, both tokens can return to the spec's 64% and 59%. The near-black foreground reads 5.25:1 on
64%.

**Icon rule.** The spec's literal rule is `.lucide { width:16px; height:16px; stroke-width:1.75px;
flex:none }`. The shipped rule keeps the stroke width and `flex: none` for every icon, but it leaves
the default size off icons drawn by the `Icon` wrapper (`data-slot="icon"`). That wrapper still sizes
through width and height attributes, and CSS would otherwise override them. UI-2 collapses this to the
literal rule when `shared/icon.tsx` moves to `size-*` classes. `tests/design-tokens.test.ts` asserts
the current split form, so UI-2 updates it at the same time.

## Legacy names

The old names are `var()` aliases of the spec tokens in the shared block, so existing screens keep
rendering. The full mapping is in `docs/ui/AUDIT.md` section 4. The notable changes:

- `--primary` is now the accent.
- `--success` no longer aliases `--brand`.
- `--danger*` and `--destructive` both point at the destructive scale.
- `--muted-foreground` is `--text-secondary`.
- `--radius-xs` is 6 px and `--radius-xl` is 14 px.
- `--dur-*` are the spec durations.

Screen packages rename utilities in the files they own. The verification package deletes the aliases.

## Guard rails

- **`pnpm lint:design`** (`web/scripts/design-lint.mjs`, also in `pnpm lint` and the vitest suite)
  forbids colour literals and raw shadows outside `globals.css`. It also forbids native selects,
  browser dialogs, per-icon stroke widths, sparkle icons, palette utilities, opacity-modified colours
  and CSS comment hazards.
- **Allowlist.** Today's violations are listed per file in `web/scripts/design-lint-allowlist.txt`.
  The list only shrinks.
- **Browser colours.** `web/src/lib/browser-colors.ts` holds the only other colour literals: the
  `theme-color` and manifest mirrors of `--background` and `--brand`.
  `web/tests/browser-colors.test.ts` pins them to the tokens.
