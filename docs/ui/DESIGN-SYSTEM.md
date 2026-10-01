# Task: enhance this project's UI and UX to a calm, professional product standard

You are working in this repository. Upgrade its user interface and user experience to the design system below.
Keep all existing functionality, data flow and routes working. Change how things **look, read and behave**, not what
they do. Where the project already has a design system, map it onto this one rather than running two in parallel.

## How to work

1. **Audit first.** Before editing anything, read how the app is built:
   - the framework, the styling approach and any component library;
   - the icon set, the fonts and the theme or dark-mode setup;
   - the shared layout, and every screen.

   Then write a short findings list covering:
   - inconsistent colours, font sizes, radii and shadows;
   - native selects or `confirm()` dialogs;
   - missing states (loading, empty, error);
   - pages without a clear primary action;
   - contrast problems;
   - mobile overflow.
2. **Tokens before screens.** Put the tokens (section 2) in one place, in both themes, and bridge them into
   whatever styling system the project uses (CSS variables plus Tailwind `@theme`, CSS modules, styled-components,
   and so on). Replace hard-coded colours, radii, shadows and durations with tokens.
3. **Primitives next.** Create or restyle the shared primitives (section 6) once. Screens compose them; screens
   never restyle them.
4. **Then screens.** Rework each screen against the page templates (section 7), the states (section 8) and the UX
   rules (section 9). Go one area at a time and keep each change reviewable.
5. **Verify.** Run the project's type check, lint and tests. Check contrast (section 11). Look at every changed
   screen in light and dark at 390, 768 and 1440 px wide.
6. **Report.** List what changed, what you intentionally left alone, and anything that needs a product decision.

Adapt to the stack you find, but prefer these libraries:
- **Primitives:** a headless, accessible library (Base UI, Radix or React Aria) for dialog, popover, menu, select,
  switch, tabs, tooltip and combobox.
- **Icons:** `lucide-react`, or the Lucide package for the framework.
- **Font:** Inter, loaded with `font-display: swap`.
- **Theming:** class-based theming (`.dark` on `<html>`), defaulting to the system setting, with no flash on load.

Do not add a heavy UI kit that brings its own look.

---

## 1. Design direction

**A calm, professional operator console.** The product should feel like a well-run instrument panel for people
making decisions: dense but quiet, and trustworthy. It must not look like a marketing site or a default template.

- **Surfaces.** Cool slate neutrals. White working surfaces sit on a slightly darker slate app background.
- **Separation.** Surfaces are separated by **1 px hairlines**. Shadows are only for floating layers: popover,
  select, menu, dialog, sheet and toast.
- **One accent colour.** Use it **only where a decision lives**: the primary action, the active navigation item,
  links, focus rings, selection and checked states. Status colours are a separate system.
- **Hierarchy through weight and tone, not size.** Body text is 14 px. Headings are only slightly larger and rely
  on weight 600 and darker ink.
- **Status is always a word plus a tone.** Never use colour alone.
- **Motion only confirms an action.** Keep it to 100–220 ms, with no bounce and no decorative animation inside the
  app. A sign-in or landing panel may have subtle ambient motion.
- **Honest, plain copy.** Say what happened and what to do next. Never show raw errors, stack traces or vendor
  messages to users.

**Never do these.**
- Sparkle or magic-wand "AI" icons.
- Gradients on controls.
- Native `<select>`, or `window.confirm` / `alert`.
- Hex colours or palette utility classes (`bg-slate-500`) inside components.
- Per-icon stroke widths.
- Uppercase text except the small "eyebrow" label.
- More than one primary button in a view or dialog.
- Uniform shadows on every card.
- Spinners as the only loading state for a page.

---

## 2. Tokens

Define every token in three places:
- its light value, in `:root`;
- its dark value, in `.dark`;
- a bridge for the styling system (for example a Tailwind v4 `@theme inline` entry `--color-card: var(--card)`).

A token missing from any one of the three is a bug. Components use **only** `var(--token)`.

### 2.1 Neutrals and text

| Token | Light | Dark | Use |
|---|---|---|---|
| `--background` | `oklch(98.6% .002 250)` | `oklch(17.5% .008 260)` | Main working panel |
| `--card` | `oklch(100% 0 0)` | `oklch(20.5% .009 260)` | Cards, inputs, bottom bar |
| `--popover` | `oklch(100% 0 0)` | `oklch(23% .01 260)` | Dialogs, popovers, menus |
| `--muted` | `oklch(96.6% .004 250)` | `oklch(24.5% .01 260)` | Hover fills, footers, table headers |
| `--muted-strong` | `oklch(93.8% .006 250)` | `oklch(28% .011 260)` | Switch track, avatar fill, skeleton sweep |
| `--border` | `oklch(92.4% .005 250)` | `oklch(28.5% .01 260)` | Every hairline |
| `--border-strong` | `oklch(60% .012 255)` | `oklch(56% .012 260)` | Input hover |
| `--input` | `oklch(66% .012 255)` | `oklch(52% .012 260)` | Input borders (3:1) |
| `--foreground` | `oklch(20.5% .015 260)` | `oklch(96% .004 260)` | Primary text |
| `--text-secondary` | `oklch(45% .016 260)` | `oklch(75% .01 260)` | Descriptions, inactive nav |
| `--text-tertiary` | `oklch(53.5% .014 260)` | `oklch(65% .01 260)` | Labels, meta, placeholders |
| `--text-disabled` | `oklch(72% .01 260)` | `oklch(46% .01 260)` | Separators, disabled |
| `--sidebar` | `oklch(96.8% .004 250)` | `oklch(14.5% .008 260)` | App background and sidebar |
| `--sidebar-hover` | `oklch(93.6% .006 250)` | `oklch(21% .01 260)` | Nav hover |
| `--sidebar-active` | `oklch(100% 0 0)` | `oklch(24.5% .011 260)` | Active nav pill |
| `--overlay` | `oklch(18% .015 260 / .42)` | `oklch(4% 0 0 / .66)` | Dialog backdrop |
| `--scrim` | `oklch(98.6% .002 250 / .92)` | `oklch(17.5% .008 260 / .95)` | Frosted sticky top bar |

In dark mode, surfaces get lighter as they come forward: sidebar, then background, then card, then popover.

### 2.2 Accent

This is indigo (hue 280), chosen so the accent never reads as green or as the info blue (hue 250). If the project
has its own brand, keep the recipe and change the hue.

| Token | Light | Dark |
|---|---|---|
| `--brand` | `oklch(48% .16 280)` | `oklch(75% .12 280)` |
| `--brand-hover` | `oklch(43% .15 280)` | `oklch(70% .12 280)` |
| `--brand-active` | `oklch(38% .135 280)` | `oklch(65% .12 280)` |
| `--brand-foreground` | `oklch(100% 0 0)` (white on accent) | `oklch(19% .035 280)` (dark text on a light accent) |
| `--brand-subtle` | `oklch(96% .018 280)` | `oklch(27% .05 280)` |
| `--brand-border` | `oklch(86% .06 280)` | `oklch(42% .09 280)` |
| `--ring` | `oklch(56% .15 280)` | `oklch(72% .12 280)` |

**Recipe for another brand.**
1. Choose an accent lightness that works as a **button fill with white text** (about 45–50% L).
2. Derive hover and active by lowering lightness about 5% per step. Never shift the hue.
3. Derive `subtle` at about 96% L with low chroma, and `border` at about 86% L.
4. In dark mode, raise the accent to about 75% L and flip its foreground to near-black.

### 2.3 Status scales

Each scale has solid, text, subtle and border values.

| Scale | Solid (light / dark) | Text (light / dark) | Subtle (light / dark) | Border (light / dark) |
|---|---|---|---|---|
| success | `52% .14 150` / `74% .15 150` | `43% .11 150` / `79% .13 150` | `96.2% .028 150` / `25.5% .04 150` | `86% .07 150` / `39% .08 150` |
| warning | `66% .14 70` / `80% .15 80` | `46% .10 65` / `83% .13 80` | `96.8% .033 85` / `26.5% .045 80` | `87% .08 80` / `41% .08 80` |
| info | `54% .14 250` / `72% .12 250` | `46% .13 255` / `79% .10 250` | `96.2% .018 250` / `25.5% .04 250` | `86% .055 250` / `39% .08 250` |
| destructive | `52% .19 27` / `64% .18 27` | `48% .18 27` / `77% .12 25` | `96.4% .016 25` / `25.5% .045 25` | `86% .07 25` / `41% .10 25` |

All values are `oklch(...)`.
- Success, info and destructive solids take white foreground text; warning takes dark text.
- `--destructive-hover` is `oklch(47% .18 27)` in light and `oklch(59% .18 27)` in dark.
- Never invent a status colour on a single page.

### 2.4 Other tokens

- **Charts:** `--chart-1…8`, in order slate, blue, amber, teal, violet, green, orange, grey. Lighten each for dark
  mode, and always read them through `var(--chart-n)`.
- **Elevation:**
  - `--elevation-raised: 0 1px 2px oklch(20% .015 260 / .05)`. Only for secondary buttons, the active nav pill and
    the main panel.
  - `--elevation-overlay: 0 12px 32px -8px oklch(20% .015 260 / .16), 0 2px 6px oklch(20% .015 260 / .06)`. For
    popovers, selects, menus and toasts.
  - `--elevation-modal: 0 28px 64px -16px oklch(20% .015 260 / .28), 0 4px 12px oklch(20% .015 260 / .08)`. For
    dialogs and sheets.
  - In dark mode, use the same shadows in black at 30–75% opacity. Borders do most of the separating there.
- **Radius:** `--radius-sm` 6 px (small buttons, tags, menu items), `--radius` 8 px (buttons, inputs),
  `--radius-lg` 12 px (cards, popovers, the main panel), `--radius-dialog` 14 px. Pills (badges, switches, chips)
  use 999 px.
- **Duration:** `--duration-fast` 100 ms (hover colours), `--duration-base` 150 ms (popovers, switches),
  `--duration-slow` 220 ms (dialogs, sheets, toasts). Entrance easing is `cubic-bezier(.16,1,.3,1)`.
- **Focus:** `--focus-shadow: 0 0 0 1px var(--brand), 0 0 0 4px var(--brand-subtle)` for inputs and open triggers.
  Everything else gets a global `:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px }`.
- **Layout:** a top bar of 56 px, a main-panel inset of 8 px and a phone bottom bar of 60 px.
- **Browser chrome:** the `theme-color` meta tag and the web manifest can't read CSS variables, so mirror the
  background and brand colours there as literal values.

---

## 3. Typography

- **Font:** Inter, falling back to `ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`.
  - Body: 14 px, line-height 1.5, letter-spacing `-.003em`, antialiased, `font-feature-settings: "cv11","ss01"`.
  - Monospace: `ui-monospace, "SF Mono", Menlo, Consolas`, 12 px.
- **Tabular numbers** (`font-variant-numeric: tabular-nums`) for every figure: money, counts, times, durations,
  table number columns and stats.

| Role | Size / line-height | Weight | Tracking |
|---|---|---|---|
| Sign-in title | 26 px | 600 | −.025em |
| Page title (`h1`) | 22 px / 1.3 | 600 | −.018em |
| Stat value | 24 px / 1.2 | 600 | −.02em |
| Dialog title | 17 px | 600 | −.012em |
| Card or section title (`h2`) | 15 px / 1.4 | 600 | −.008em |
| `h3` and body | 14 px / 1.45–1.5 | 600 / 400 | — |
| Buttons, nav, menu and select items, option labels | 13.5 px | 500 | — |
| Form labels, descriptions, table cells, alerts | 13 px | 500 labels / 400 text | — |
| Hints, badges, meta, table headers | 12 px | 400–500 | — |
| Nav group label | 11.5 px | 500 | +.02em |
| Eyebrow (the only uppercase) | 12 px | 500 | +.04em |
| Phone tab label | 11 px | 500, 600 when active | — |

**Text roles.** A page intro is secondary text at max 68ch with line-height 1.55. Section and card descriptions are
13 px secondary at max 72ch. Hints are 12 px secondary. Placeholders are tertiary.

**Copy rules.**
- Use sentence case for every label, button, heading and tab.
- Buttons are verbs ("Save draft", "Send invite").
- Busy labels become a gerund plus an ellipsis ("Saving…").
- Page descriptions are one sentence.
- An empty state is one sentence plus one action.
- Error messages say what happened and the next step.
- Never show internal IDs, vendor error text or technical jargon.

---

## 4. Spacing, layout and density

- **Page container:**
  - Default: max-width 1200 px, centred, padding `28px 32px 72px`.
  - **Wide** (1440 px) for dense tools: detail records, calendars, settings with side nav, analytics.
  - **Narrow** (880 px) for forms and reading.
  - Padding becomes `20px 20px 56px` at 900 px and below, and `16px 16px 48px` at 640 px and below.
- **Vertical rhythm:**
  - page header to content: 24 px (18 px on phones);
  - between sections: 32 px;
  - section heading to content: 14 px;
  - between fields in a form: 16 px;
  - label to control: 6 px.

  Use varied, intentional spacing, not the same padding everywhere.
- **Grids:** two- and three-column grids with 16 px gaps, using `minmax(0,1fr)` columns. A three-column grid
  becomes two columns at 900 px and below, and every grid becomes one column at 640 px and below. Stat grids use
  `repeat(auto-fit, minmax(180px,1fr))` with 12 px gaps.
- **Control heights:**

  | Control | Height |
  |---|---|
  | Inputs, selects | 36 px (small select 30 px) |
  | Buttons | 34 px (small 28, large 40) |
  | Icon buttons | 32 px (small 28) |
  | List rows | at least 56 px |
  | Menu items | 34 px |
  | Select items | 32 px |
  | Table headers | 36 px |
  | Phone touch targets | at least 48 px |
- **Card padding:** header `16px 20px`, body `16px 20px 20px`, footer `12px 20px`. Horizontal padding drops to
  16 px on phones.
- **Density:** comfortable-compact. Table cells use `10px 14px` padding.

---

## 5. Icons and motion

### Icons

- Use **Lucide** only. Set one global rule: `.lucide { width:16px; height:16px; stroke-width:1.75px; flex:none }`.
  Override size by context, never stroke width.

  | Size | Where |
  |---|---|
  | 12 px | Chip remove |
  | 14 px | Small buttons, text links, stat labels |
  | 15 px | Selects, segmented controls, back link |
  | 16 px | Default |
  | 17 px | Sidebar nav |
  | 18 px | Empty-state tile |
  | 20 px | Phone tab bar |
- **Decorative by default** (`aria-hidden="true"`), with visible or screen-reader-only text beside the icon. An
  icon-only button needs an `aria-label`.
- Icons inherit `currentColor`: secondary or tertiary when inactive, and the accent on the active nav item.
- **Intent map.** Pick one icon per intent and use it everywhere.

  | Intent | Icon |
  |---|---|
  | Add | `Plus` |
  | Back | `ArrowLeft` |
  | Open / forward | `ArrowRight`, `ChevronRight` |
  | External | `ExternalLink` / `ArrowUpRight` |
  | Close / dismiss | `X` |
  | Delete | `Trash2` |
  | Edit | `Pencil` |
  | Search / no matches | `Search` / `SearchX` |
  | Success / check | `CircleCheck` / `Check` |
  | Warning | `TriangleAlert` |
  | Error | `CircleAlert` |
  | Info | `Info` |
  | Refresh / retry | `RefreshCw` (spins while busy) |
  | More actions | `MoreHorizontal` |
  | Select affordance | `ChevronsUpDown` |
  | Disclosure | `ChevronDown` |
  | Mobile menu | `Menu` |
  | Home | `House` |
  | Agents (voice and video) | `Headset` (`AGENTS_ICON` in `nav-config.ts`); `Bot` only for coding agents over MCP |
  | Calendar | `CalendarDays` |
  | People | `Users`, `UserPlus`, `UserMinus` |
  | Security | `KeyRound`, `ShieldCheck`, `Lock` |
  | Sign in / out | `LogIn` / `LogOut` |
  | Notifications | `Bell` |
  | Files | `FileText`, `Upload` |
  | Chat | `MessagesSquare` |
  | Money | `Coins`, `Wallet` |
  | Connections | `Link2`, `Plug` |
  | Theme | `Sun`, `Moon`, `Monitor` |
  | Settings / admin | `Cpu`, `ChartNoAxesCombined` or the nearest literal |
- **Third-party logos:** use the real mark, in monochrome `currentColor` if its brand colour fails contrast. Always
  pair it with the name, and fall back to a neutral generic icon. In this console that is `VendorMark`: official marks
  from Simple Icons (CC0), then Lobe Icons (MIT), then the companies' own files, in one table
  (`components/shared/vendor-marks.ts`), drawn in `currentColor`, with the tinted monogram as the fallback. A brand
  that offers no one-colour version keeps its own fills (only Microsoft Outlook). docs/ui/VENDOR-MARKS.md has the
  sources and the policy.
- **Our logo.** `LkapLogo` (`components/shared/lkap-logo.tsx`) is a rounded `--brand` tile with a
  `--brand-foreground` live dot and two rising bars, drawn inline from the tokens. The full logo adds "LKAP" in Inter
  600 and an optional product name in secondary text. It leads the sidebar (28 px mark, 15 px wordmark, a link to
  Overview), stands alone in the collapsed rail, the phone top bar and the sign-in card, and the app icons
  (`app/icon.svg`, `favicon.ico`, `apple-icon.png`, the manifest icons) are drawn from the same geometry by
  `scripts/gen-app-icons.mjs`. It never stands in for a vendor, and vendor marks never stand in for it.

### Motion

- Hover and colour changes: `--duration-fast`.
- Button press: `transform: scale(.98)`.
- Popover, select and menu: open with opacity plus `scale(.97)` from the anchor's transform-origin over 150 ms.
- Dialog: opacity plus an 8 px rise and `scale(.98)` over 220 ms with the entrance easing.
- Backdrop: fades.
- Side sheet: slides 24 px in from its edge.
- Toast: rises 8 px and fades in over 220 ms.
- Switch thumb: slides over 150 ms.
- Spinner: 14 px, 2 px ring, accent-coloured top edge, 0.8 s linear rotation.
- Skeleton: `--muted` block with a `--muted-strong` gradient sweeping across every 1.4 s.
- Live or recording indicator: a pulsing dot (opacity 1 → .35 over 1.6 s).
- **Reduced motion:** add a global `@media (prefers-reduced-motion: reduce)` rule that sets animation and transition
  duration to 1 ms and animation iteration count to 1. Also disable any feature-specific keyframes.
- Animate only `transform`, `opacity` and colours. Never animate width, height, top or margin.

---

## 6. Primitives

Build these once, then compose screens from them. Screens may lay primitives out but must not restyle them.

### 6.1 Buttons

- **Base:** 34 px tall, padding `0 13px`, radius 8 px, 13.5 px / 500, line-height 1, gap 6 px, 16 px icon, no
  wrapping. Pressed: `scale(.98)`. Disabled: opacity .5 with a `not-allowed` cursor.

| Variant | Fill | Text | Border | Hover | Use |
|---|---|---|---|---|---|
| primary | `--brand` | `--brand-foreground` | — | `--brand-hover`, then `--brand-active` when pressed | The **one** main action |
| secondary | `--card` | `--foreground` | `--border` plus raised shadow | `--muted` | Alternatives, toolbar actions |
| ghost | transparent | `--text-secondary` | — | `--muted` fill, `--foreground` text | Low-emphasis actions |
| danger | `--destructive` | white | — | `--destructive-hover` | Only **inside** a confirmation step |
| danger-outline | `--card` | `--destructive-text` | `--destructive-border` | `--destructive-subtle` | The entry point to a destructive flow |

- **Sizes:**
  - small: 28 px, padding `0 10px`, 13 px, radius 6 px, 14 px icon;
  - large: 40 px, padding `0 18px`, 14 px;
  - icon: square 34 px (28 px when small);
  - block: full width.
- **Text button:** inline, accent colour, 13 px / 500, underlined on hover with a 3 px offset. There are
  destructive and neutral variants.
- **Icon button:** 32 px (28 px small), borderless, secondary ink, `--muted` on hover. Used for row menus, dismiss
  and the hamburger.
- **Busy state:** disable the button and change its label ("Saving…"). There is no spinner inside text buttons.
  Icon buttons swap their icon for the spinner.
- **Order:** the primary button comes **last** (right-most), with Cancel to its left. Button groups use an 8 px gap
  and wrap.

### 6.2 Form controls

- **Inputs and textareas:**
  - at least 36 px tall, padding `7px 11px`, `--input` border, radius 8 px, `--card` fill;
  - hover: `--border-strong` border;
  - focus: `--brand` border plus `--focus-shadow` (no outline);
  - disabled: `--muted` fill, secondary text;
  - placeholder: tertiary.

  Textareas resize vertically only, with line-height 1.55.
- **Field:** a grid with a 6 px gap. Labels are 13 px / 500. "(optional)" is 12 px tertiary. Hints are 12 px
  secondary. Inline errors are 13 px destructive text, or a form-level error block. Two- and three-column field
  rows collapse on phones.
- **Checkbox and radio:** native 16 px controls with `accent-color: var(--brand)`, inside a label row with the
  input nudged 2 px down to align with the text.
- **Option card:** a bordered, clickable label wrapping a native radio or checkbox plus a bold title and a small
  description. When checked it gets the brand border and brand-subtle fill, done in pure CSS with
  `:has(input:checked)`. Use option cards instead of bare radios when the choice needs explaining.
- **Switch:** a 34 × 20 pill track (`--muted-strong`, `--brand` when on) with a 16 px thumb that moves 14 px. It
  sits in a row with a bold label and small description on the left and the switch on the right.
- **Input with icon:** a leading 16 px tertiary icon 11 px from the left, with the input padded 34 px on the left.
  Use it for search and key fields.
- **Search field:** type search with a leading `Search` icon. Escape clears it, and a trailing `X` appears only
  when it has a value. Filter synchronously as the user types.
- **Passwords:** account passwords get a show/hide toggle. Secrets such as API keys are **write-only**: masked, and
  never shown again after saving.
- **Chip input** (emails, tags, URLs):
  - Enter, comma and semicolon commit a chip; pasting a list splits it.
  - Invalid chips are **kept and flagged** with a destructive tint, never silently dropped.
  - Chips are 26 px pills with an initial and a remove `X`.
  - An `@` mention may open a suggestion list.
  - Changes are announced through a polite live region.
- **Validation:** validate on submit and show server errors in the form-level block. Validate inline only where it
  prevents a mistake: chips, typed confirmation, password match.
- **File input:** style the selector button (32 px, bordered).

### 6.3 Select, combobox and pickers

- **One custom select** built on a headless primitive. It replaces **every** native `<select>`.
  - **Trigger:** at least 36 px (small 30 px), `--input` border, ellipsised value, 15 px tertiary `ChevronsUpDown`
    on the right. When open it gets the brand border plus `--focus-shadow`.
  - **Popup:** at least as wide as the trigger, at most `min(92vw, 440px)` wide and `min(360px, available height)`
    tall. Radius 12 px, overlay shadow, 5 px padding.
  - **Items:** 32 px tall, `--muted` when highlighted. The selected item is weight 500 with a trailing accent
    `Check`.
- **Long lists** use a searchable combobox with grouped results (for example "Current", "Recommended", "All"), a
  cap on the number of rows rendered, and a free-text option when custom values are allowed.
- **Full keyboard support:** arrow keys, Enter, Escape, Home/End and PageUp/PageDown.

### 6.4 Overlays

- **Dialog:**
  - A backdrop (`--overlay`) plus a popup: width `min(100vw − 32px, 560px)` (large 760 px, extra-large 980 px),
    max height `min(88dvh, 860px)` with internal scroll, padding 24 px, radius 14 px, modal shadow.
  - A close `X` sits in the top right: 30 px, 16 px from the edges.
  - Content: a 17 px title, a 13.5 px secondary intro, then a body grid with a 16 px gap.
  - **Sticky footer:** it bleeds to the dialog edges with a `--muted` fill, a top hairline and rounded bottom
    corners, so the actions stay visible while the body scrolls.
  - On phones: width `100vw − 16px`, padding 20 px.
  - Destructive dialogs use `role="alertdialog"`. Focus is trapped while open and returns to the trigger on close.
- **Popover:** at least 200 px wide, 6 px padding, radius 12 px, overlay shadow, scales in from its anchor.
- **Menu** (row "…" actions): an icon-button trigger, with the popup aligned to the trigger's end at a 4 px offset.
  - Items are 34 px with a 16 px secondary icon.
  - A 12 px tertiary label heads each group. Separators are hairlines that bleed to the edges.
  - **A destructive item always comes last, after a separator, in destructive text.**
- **Side sheet:** used for editing an item in context without leaving the list. It opens from the right, is
  `min(100vw, 520px)` wide, has a left border and the modal shadow.
- **Mobile navigation:** a sheet from the left, `min(292px, 100vw − 40px)` wide, containing the full sidebar.
- **Tooltip:** only for supplementary information (250 ms delay, max 320 px). Never put essential information only
  in a tooltip.
- **Toasts:** bottom-right, 24 px from the edges (16 px on phones), overlay shadow, entrance animation.
  - **Success and info toasts auto-dismiss after 6 s. Errors and warnings stay** until dismissed.
  - Errors use `role="alert"` (assertive). Everything else uses `role="status"` (polite).
  - Use one toast primitive for the whole app.

### 6.5 Feedback

- **Alert:**
  - Tones: info, success, warning, danger, brand and neutral, with the icons `Info`, `CircleCheck`,
    `TriangleAlert` and `CircleAlert`.
  - Styling: padding `10px 12px`, radius 8 px, 13 px text, and the tone's subtle fill, border and text colour.
  - It has an optional bold title and right-aligned actions such as Retry.
  - Its role is `alert` for danger and `status` otherwise.
- **Empty state:** centred, padding `40px 24px`, a dashed hairline border on a card surface. Include a 40 px
  muted icon tile with an 18 px icon, a 14 px / 600 title, a 13 px description (max 46ch) and **one** action.
- **No matches:** a separate empty state using `SearchX`: "No {items} match “{query}”", then "Try a different
  name, word or category.", then a **Clear filters** action.
- **Skeleton:** 14 px lines with a ragged right edge (the last line at 60%). **Mirror the real layout.**
- **Loading row:** a spinner plus a 13 px secondary label with `role="status"`, for small in-card loads.
- **Progress:** a bar with `role="progressbar"` and full `aria-value*` attributes, plus a step list where the
  current step has `aria-current="step"`.

### 6.6 Status, badges and tags

- **Badge and status pill:** 22 px tall, padding `0 8px`, 12 px / 500, hairline border, radius 999 px.
  - Status pills always show a 6 px `currentColor` dot.
  - Tones: success, warning, info, danger, brand (live, with a **pulsing** dot) and neutral.
- **Mapping lifecycle states to tones:**

  | Tone | States |
  |---|---|
  | success | done, ready, approved, sent |
  | warning | waiting, draft, needs review |
  | info | scheduled, in progress, processing |
  | danger | failed, needs attention |
  | brand | live |
  | neutral | stopped, cancelled |

  Keep the labels human ("Ready to review", "Needs attention", "Couldn't join") and keep them in one shared map.
- **Tag:** 22 px, **squarer** (radius 6 px), card fill, secondary text. Use tags for freeform labels such as topics
  and dates. Space them 6 px apart.

### 6.7 Data display

- **Card:** hairline border, radius 12 px, **no shadow**.
  - Header: a title (`h2`/`h3`), a 13 px description and right-side actions, over a bottom hairline.
  - Body: padded as in section 4.
  - Footer: `--muted` fill, top hairline, right-aligned actions (or split to both sides).
  - Inset panel: a `--muted` box inside a card for secondary content.
- **List card:** a bordered container whose rows are separated by hairlines. Rows are at least 56 px tall with
  12 px gaps. Each row reads:
  - a leading avatar, mark or monogram;
  - a title plus a meta line (secondary, tabular times);
  - a trailing badge, count or "…" menu, or `ArrowRight` if the row is clickable.

  Clickable rows get a pointer cursor and a `--muted` hover.
- **Table:** inside a bordered, rounded wrapper that scrolls horizontally.
  - Headers: 36 px, `--muted` fill, 12 px / 500 secondary.
  - Cells: padding `10px 14px` with hairlines between rows. Rows get a `--muted` hover.
  - Number columns align right and use tabular figures.
- **Meta list:** a two-column `<dl>` with 12 px tertiary terms and 13.5 px tabular values. One column on phones.
- **Stat card:** a 12.5 px label with a 14 px icon, a 24 px / 600 tabular value and a 12 px hint.
- **Avatar:** 22, 28 or 40 px circles with initials on `--muted-strong`. Use the first letters of the first and
  last words, and fall back to initials when a photo fails to load.
- **Segmented control:** a `--muted` track with 3 px padding and 28 px items. The selected item gets a card fill,
  hairline and raised shadow, and an optional count sits beside each label. Use it for 2–8 peer **filters or
  modes**. It scrolls horizontally on phones.
- **Tabs:** 40 px text tabs with a 2 px **accent underline** on the selected tab, a bottom hairline and count pills.
  Use tabs for **sections of one object or page**.
- **Theme switcher:** three icon buttons (System, Light, Dark) in a muted track, placed in the account menu.
- **Keyboard hint:** a 20 px key cap with a 2 px bottom border.
- **Charts** (if any) use the chart tokens, hairline gridlines, tabular labels and a text alternative.

---

## 7. App shell and page templates

### 7.1 Shell (desktop, wider than 820 px)

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ Sidebar 248px│  ┌─ main panel: inset 8px, radius 12, hairline, raised ──┐ │
│ app bg       │  │ Top bar 56px · sticky · frosted scrim + blur(12px)    │ │
│ Logo + name  │  │  Workspace / Current page            [status] [bell]  │ │
│ Nav (group)  │  ├───────────────────────────────────────────────────────┤ │
│ GROUP LABEL  │  │  <main id="main-content">  page → header → content    │ │
│ Nav …        │  │                                                       │ │
│   (spacer)   │  │                                                       │ │
│ Account ⇅    │  └───────────────────────────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────────────────────────┘
```

- **Sidebar:** sticky and full height, padding `12px 10px 10px`, scrolls on its own, on the app background colour.
  Group items under small labels by job; don't pile everything into one list.
  - **Nav link:** 34 px, 13.5 px / 500, secondary text, 17 px icon, `--sidebar-hover` on hover.
  - **Active link:** a `--sidebar-active` pill with a hairline border, raised shadow, foreground text, **accent
    icon** and `aria-current="page"`.
  - **Right-edge extras:** an optional tabular count, or a 7 px pulsing dot for live activity.
- **Account menu** at the foot of the sidebar, above a hairline: avatar, name, email, then a popover with the
  workspace switcher, profile, appearance (theme switcher) and sign out.
- **Main panel:** inset 8 px from the viewport, `--background` fill, hairline border, radius 12 px, raised shadow.
- **Top bar:** 56 px, sticky, `--scrim` with a 12 px backdrop blur, bottom hairline.
  - Left: a breadcrumb (context, "/", current page with `aria-current`).
  - Right: global status and notifications.
  - Keep search on each list rather than in a global box unless the product needs one.
- **Skip link** to `#main-content` as the first focusable element.

### 7.2 Shell at 820 px and below

- The sidebar is removed. The panel goes full-bleed (no inset, border, radius or shadow), and the top bar pads
  16 px.
- A hamburger (`Menu`) in the top bar opens the left navigation sheet.
- **At 640 px and below**, add a **phone bottom tab bar** with the 3–5 most-used destinations for the person's role
  plus "More" (which opens the nav sheet).
  - Fixed to the bottom, 60 px tall plus `env(safe-area-inset-bottom)`, on `--card` with a top hairline.
  - 48 px targets. The icon (20 px) sits in a 48 × 26 pill that turns `--brand-subtle` with accent ink when active.
  - Labels are 11 px.
  - Show badges for live counts, capped at "9+".
  - Pad the page by the bar's height, and lift toasts above it.
  - Hide the bar on full-screen task pages that have their own bottom action bar.
- Hide top-bar text labels, leaving icons with `aria-label`s.

### 7.3 Page template

```
section.page[.wide|.narrow]
  header.page-header         flex · space-between · align-end · wrap · gap 16/24
    .page-header-text
      back link?             ArrowLeft · 28px · 13px secondary · −8px left margin
      eyebrow?               12px uppercase tertiary
      h1 + status badge?     22px/600
      p.intro                one sentence · secondary · 68ch
    .page-actions            secondary…, then ONE primary (last)
  page-level alerts
  content                    cards / grids / list cards / tables
```

On phones the header aligns to the start and the actions go full width.

### 7.4 Archetypes

- **Overview / dashboard:**
  - A greeting and one-sentence purpose, with the primary "New …" action.
  - A stat grid of 3–6 counts, each with a hint.
  - Two columns: a recent-items list card with "See all", and an aside with "how it works" or shortcuts.
  - No chart unless the chart answers a question.
- **List / library:**
  - A header with actions.
  - A segmented status filter with counts, plus a search box (once the list has 6 or more items).
  - A list card of rows.
  - Distinct "no matches" and "nothing yet" empty states.
- **Detail / record** (wide):
  - A back link, a title plus status badge, and meta.
  - Actions: refresh, a danger-outline exit, and the primary next step.
  - Page-level alerts.
  - A **main column** of work cards (review, edit, share) and a **side column** of facts cards (details, people,
    settings), ending in a **Danger zone** card with a typed confirmation.
  - The side column stacks under the main one on narrow screens.
- **Settings:** a sticky left anchor nav with scroll-spy, plus a column of cards.
  - Read-only versions for people who can't edit.
  - Save per card, with a success toast.
- **Form / wizard:** a narrow page or a large dialog with a sticky footer. Group fields, mark optional fields
  rather than required ones, and put explicit consent checkboxes before irreversible or sensitive actions.
- **Chat / assistant:**
  - A left rail of collections and conversations with search.
  - A main thread with streamed answers and **citations back to sources**.
  - A composer dock with filters.
  - A segmented mode switch if there are several modes (for example Ask / Sources).
- **Analytics:** a date-range picker and Refresh in the header, then tabs.
  - The overview tab has a stat grid, an "estimates" disclaimer, breakdown cards and health badges (Healthy / In
    progress / Needs review).
  - Detail tabs are tables with export.
- **Sign-in:** two columns.
  - Left: a 380 px form card with a 26 px title.
  - Right: a showcase panel on the sidebar colour with a subtle dotted-grid texture, a soft blurred accent glow
    and a small illustration of the product's outcome. The showcase hides at 1080 px and below.
  - Forgot-password always shows the same "Check your email" screen, so it never reveals whether an account exists.
- **Long-running task page:** a progress bar plus a named step list with the current step marked.
  - Screen readers hear each change through an `sr-only` status line.
  - The user can leave, and is notified when the task finishes.
  - A failed task offers Retry.

---

## 8. States: design every one

For every screen and card, design each of these:

1. **Loading:** a skeleton that mirrors the layout, or a loading row for small in-card loads. Never a blank area.
2. **Empty ("nothing yet"):** one sentence plus the one action that fills it.
3. **No matches:** different copy from "nothing yet", with **Clear filters**.
4. **Error:** a plain explanation plus a next step (Retry, "Check the connection", "Ask an admin"). Keep entered
   data.
5. **Permission-denied:** a complete alternative, such as a read-only view or a locked action that says "Ask an
   admin". Never a gap and never a lone padlock. Don't render controls the person can't use. The server must
   enforce the same rules, and data the person may not see should never be sent to the client.
6. **Success:** a toast or an inline success alert, and the UI reflects the new state immediately.
7. **Busy:** disabled controls, gerund labels, spinning refresh icons.
8. **Offline or unavailable:** say so, and say what will happen (for example "Uploads resume when you're back
   online").

---

## 9. UX rules

- **One primary action per view and per dialog**, placed last. Everything else is secondary, ghost or a text
  button.
- **Destructive actions**, never a browser confirm:
  - **Inline two-step** for small, recoverable removals: the row swaps to Cancel / Confirm in place.
  - **Confirm dialog** (`alertdialog`) that states exactly what will happen, with Cancel and a danger button.
  - **Typed confirmation** (type `DELETE`) for irreversible, data-destroying actions. List what will be removed.
- **Search on every list** once it has **6 or more items**, or while a query is active.
  - Matching is client-side, **accent-insensitive**, and every word must match somewhere.
  - Highlight the matched text. Escape clears the search.
  - Server-backed search runs on submit.
  - Remember a list's filters and selection per person (local storage, validated on read).
- **Status is never colour alone.** Use a word plus a tone, and a dot for live states.
- **Time is personal.**
  - Show times in the person's time zone and chosen 12- or 24-hour format, with an auto option.
  - Label zones in a human way ("IST (Kolkata)").
  - Use relative times where they help ("just now", "5 min ago", "in 2 h").
  - Always use tabular figures.
- **Optimistic updates roll back.** Snapshot the state, apply the change, then save. On failure restore the
  snapshot and explain in an alert. Ignore stale responses when the person has moved on.
- **Long work is visible and leavable.** Show progress with named steps, let the person leave, and notify them when
  it finishes. Poll faster while something is active, and pause polling while the tab is hidden.
- **Consent and privacy are explicit.**
  - Sensitive actions (recording, sending on someone's behalf, writing to external systems) need an explicit
    checkbox or a preview of exactly what will be sent.
  - Secrets are write-only.
  - One-time links carry their token in the URL fragment, and the fragment is stripped at once.
- **AI output is reviewed, not trusted.**
  - Label AI-generated content with its source (for example "Generated with {model}").
  - Keep a human approval step before anything is sent or stored as final.
  - Show evidence or citations for suggestions.
  - Offer Approve / Edit / Dismiss.
- **Notifications:**
  - A bell with an unread count (capped at "99+").
  - A popover with All / Unread, search, Today / Earlier groups, a per-item dismiss, "Mark all as read", "Clear
    read" and "Clear all" (both confirmed), and a separate "cleared" empty state.
  - Clicking an item takes the person to the right place.
- **Forms remember progress.** Never lose typed input on an error or a failed save.
- **Navigation is predictable.** Back links name where they go ("Back to meetings"), and the active location is
  always visible in the sidebar, breadcrumb and tab bar.

---

## 10. Accessibility and responsive

**Accessibility**
- A skip link, one `h1` per page and sequential heading levels.
- `aria-current` for the active page and the active step.
- Live regions:
  - errors: `role="alert"`;
  - status and saving: `role="status"`;
  - screen-reader-only announcers for dynamic changes such as chip edits and progress stages.
- Icons are `aria-hidden`. Icon-only buttons have an `aria-label`. Unread and status dots have `sr-only` text.
- Headless primitives handle focus trapping and return. Custom comboboxes implement the full ARIA pattern.
- Contrast is 4.5:1 for text and 3:1 for UI and large text, **in both themes**.
- Touch targets are at least 48 px on phone navigation. Controls are at least 28 px, and primary actions 34–40 px.
- Reduced motion is honoured everywhere.

**Responsive**

| Width | What changes |
|---|---|
| ≤ 1080 px | Two-column sign-in becomes one column |
| ≤ 900 px | Page padding becomes 20 px; three-column grids become two |
| ≤ 820 px | Sidebar becomes a sheet; the panel goes full-bleed |
| ≤ 640 px | Everything is one column; phone tab bar appears; dialogs are `100vw − 16px`; card padding is 16 px; top-bar labels hide |
| 390 px | **No horizontal page scroll.** Tables scroll inside their wrapper; segmented controls scroll horizontally |

Honour `env(safe-area-inset-*)` on any bar fixed to the bottom. Use `100dvh`, not `100vh`.

---

## 11. Verification

Before calling it done:

- **Contrast check.** Write or reuse a small script that parses the OKLCH tokens from both themes, converts them to
  sRGB luminance and asserts WCAG ratios for:
  - foreground, secondary and tertiary text on card, background, muted and sidebar surfaces;
  - input, strong-border and ring colours at 3:1;
  - the accent against its foreground;
  - every status text and dot.

  Fix failures by moving lightness, never by lowering the bar.
- **Design lint.** Add a grep-based check to CI that fails on:
  - hex, `rgb()` or `oklch()` colours outside the token file;
  - raw px `box-shadow` outside the tokens;
  - native `<select>`, `window.confirm` and `alert(`;
  - per-icon `strokeWidth`;
  - sparkle icons;
  - palette utility classes.
- **CSS safety.** A stray `*/` inside a CSS comment can break the whole stylesheet. Check for it.
- **Render check.** Load every page, as every role, in the running app and fail on console errors. Type checks
  can't catch everything.
- **Visual check.** Look at light and dark at 390, 768 and 1440 px, including every empty, error and busy state.
- **Existing tests stay green.** Update selectors only where the markup legitimately changed, and never weaken an
  assertion to make it pass.
- **Styleguide page.** Build or update a specimen page showing tokens, type, buttons, statuses, fields, segmented
  controls, alerts, stats, a card and an empty state in both themes. It is how people review the system.

## 12. Definition of done for each screen

- [ ] Page template followed: one-sentence description and exactly one primary action, placed last.
- [ ] Composed from primitives only, with layout-only styles. Tokens only: no raw colours, radii, shadows or font
      sizes outside the scale.
- [ ] Icons from the intent map, at the right size, `aria-hidden`. No sparkles.
- [ ] All eight states designed (section 8).
- [ ] Search on lists with 6 or more items. Destructive actions use a two-step, confirm or typed pattern.
- [ ] Status shown as a word plus a tone. Times personal and tabular.
- [ ] Optimistic writes roll back with a message, and stale responses are ignored.
- [ ] Keyboard, screen-reader, contrast and reduced-motion checks pass.
- [ ] Works in light and dark, and at 390 px without horizontal scroll.
- [ ] Copy is sentence case, plain and jargon-free, with gerund busy labels and a next step on every error.
