# V6-24 live check for the five next-block renderers

Not run in this sandbox, because no dev server, worker restart or `pnpm build` was permitted here (ask
#242). This is the coordinator's walk once a worker on V6-23's tools and a web build of this
package are both up. Prerequisites: the worker restarted (V6-23's five builtin tools
`request_signature`, `show_chart`, `start_timer`, `show_code` and `cart_set` must already be
registered), no migration, a `Demo — ` agent with a `composite` panel carrying one block of each
of the five types (`signature`, `chart`, `timer`, `code`, `cart`), or reuse `FIXTURE_LAYOUT`'s ids
(`sign`, `claims_chart`, `timer`, `record`, `order`) by seeding the panel the same way. The PLAN's
live rules apply: `Demo — ` objects only, a Builder key minted for the run and revoked after,
fictional names and `example.com` addresses. Screenshots at desktop and 375 px go in this brief
once it is run.

## 0. Offline gates already green (this package)

`pnpm exec tsc --noEmit`, `pnpm lint` (0 errors), `pnpm exec vitest run` (153 files / 2696 tests,
including the 28 new cases in `web/tests/panel-next-blocks.test.tsx` and the S6-8/S6-9 fixes'
tests). Not run here: `e2e/a11y.spec.ts`'s five new `scene=blocks` routes (need `pnpm dev`/`next
start`), and `scripts/check-bundle.mjs` (needs a `next build` log). All five renderers are
`React.lazy`, so `/s/[slug]`'s first load should be unaffected, but this was not measured.

## 1. Text chat, the signature answers "needs the web page"

1. `chat_start` on the `Demo — ` agent, then `chat_send("Can you have me sign the estimate?")`.
2. The model calls `request_signature`. On a text channel the tool answers at once with "Nothing
   can be signed here. A signature needs the web page." No block state changes and no event
   recorded. Confirm via `chat_send` that the model relays this in plain words.
3. `show_chart`, `start_timer` and `show_code` all work on text chat too (they are not
   voice/web-only). `chat_send("Show me a chart of claims by month")` should produce a
   `show_chart` call and a description of it in `describe_panel`'s next read.

## 2. Voice session, the full walk (needs the console or a WebRTC test harness)

Connect to the session's room with a browser (the console's session page, or `/s/<slug>` directly)
so the five blocks are visible.

### Signature

1. Say "Can you have me sign the repair estimate?" The model calls `request_signature`. The
   `signature` block shows the disclosure wording as plain text, a small drawing pad and Sign / Not
   now (unless the block's `allow_decline` is off).
2. Draw something in the pad with the mouse or a touchscreen. Confirm no network activity happens
   yet (the strokes are local only. Watch the room's `lkap.ui.ink` stream in devtools/the listen-in
   tab and confirm nothing is sent there for this block).
3. Tap Sign. Confirm: (a) `block_submit {values: {signed: true}}` fires, (b) shortly after, the
   worker's `lkap.ui.request {method: "snapshot"}` arrives for this block and the page answers with
   one PNG on `lkap.ui.upload` (≤ 1 MiB), (c) the block settles to "Signed" with a timestamp and (if
   `panel.assets` resolves the stored picture) a small thumbnail, (d) a `signature` session event is
   recorded with a `text_hash` and no picture bytes, and (e) the model acknowledges briefly.
4. Repeat and tap **Not now** instead and confirm `{signed: false}`, no snapshot request, the block
   shows "Not signed", and the model respects it without pressing further.
5. Repeat once more and **speak over the request** (barge-in) before signing, and confirm the block
   goes to `cancelled` (nothing to press) and the model asks whether to try again (R-V5-1).

### Timer

1. Say "Give me two minutes to find my policy number." The model calls `start_timer`. The `timer`
   block shows a countdown from the page's own clock (confirm it does **not** jump if the browser's
   clock is skewed from the server's, which is the acceptance test for this).
2. Let it run out (or start one with `duration_s=30` for a faster check). Confirm `timer_ended` is
   recorded, the model is told in one line and says something appropriate, and the block shows
   "Time's up".

### Chart

1. Ask for "a chart of how many claims came in each month." Confirm `show_chart` renders a bar
   chart with the given points, the caption if given, and that the accessible table under it is
   screen-reader-only unless the block's `show_table` config is on (toggle it in the panel editor to
   confirm the table becomes visible).
2. Ask for "just the total number of claims this month" (a `number` chart) and "our capacity as a
   percentage" (a `gauge`) to see those two kinds render.

### Code

1. Ask the agent to "show me the raw policy record." Confirm `show_code` renders the JSON (or
   whichever language) as plain fixed-width text, never as Markdown (a payload with a literal
   ```` ``` ```` fence inside it must render as visible text, not break the block).

### Cart

1. Ask for "an order for two water filters and a technician visit, with a discount." Confirm
   `cart_set` renders the lines, the discount as a negative adjustment, and a total formatted in the
   agent's currency (try a non-USD agent, e.g. `EUR`, to confirm `Intl.NumberFormat` follows the
   block's own `currency` rather than always showing `$`). The total shown must exactly match what
   the tool computed — never a value the renderer recalculates itself.

## 3. Mobile check (375 px)

Resize to 375 px (or use a phone) for each of the five blocks above: the signature pad stays
usable with a finger, the chart's SVG scales without horizontal scroll, the timer's digits don't
wrap awkwardly, the code block scrolls horizontally (or wraps, with `wrap` on) instead of
overflowing the panel, and the cart's rows stay readable without truncated prices.

## 4. Clean-up

Delete the `Demo — ` agent and any test session; revoke the Builder key.
